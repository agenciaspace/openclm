"""User-authorized Salesforce opportunity lookup and local contract linkage."""

import base64
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlalchemy.orm import Session

from .config import validate_salesforce_origin
from .models import (
    AuditEvent,
    Contract,
    SalesforceConnection,
    SalesforceLink,
    SalesforceState,
    User,
    now,
)
from .oauth import decrypt, encrypt
from .security import current_user, digest, get_db, require_roles

router = APIRouter(prefix="/api/v1/integrations/salesforce", tags=["Salesforce"])
FIELDS = "Id, Name, Account.Name, StageName, Amount, CloseDate"


def callback_url(cfg):
    return cfg.app_url.rstrip("/") + "/api/v1/integrations/salesforce/callback"


def exchange(cfg, data):
    with httpx.Client(timeout=20, trust_env=False) as client:
        response = client.post(
            cfg.salesforce_login_url.rstrip("/") + "/services/oauth2/token",
            data={
                **data,
                "client_id": cfg.salesforce_client_id,
                "client_secret": cfg.salesforce_client_secret,
            },
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result.get("access_token"), str) or not result["access_token"]:
            raise ValueError("Missing access token")
        return result


@router.get("/status")
def status(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    connection = db.get(SalesforceConnection, user.id)
    return {
        "configured": request.app.state.settings.salesforce_enabled,
        "connected": bool(connection),
        "instance_url": connection.instance_url if connection else None,
    }


@router.post("/connect")
def connect(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "editor")),
):
    cfg = request.app.state.settings
    if not cfg.salesforce_enabled:
        raise HTTPException(503, "O administrador precisa configurar o aplicativo Salesforce.")
    if request.state.auth_token.kind != "session":
        raise HTTPException(403, "Conecte pelo navegador.")
    state, verifier = secrets.token_urlsafe(40), secrets.token_urlsafe(64)
    db.execute(delete(SalesforceState).where(SalesforceState.expires_at < now()))
    db.add(
        SalesforceState(
            state_hash=digest(state),
            user_id=user.id,
            session_id=request.state.auth_token.id,
            verifier_encrypted=encrypt(cfg, verifier),
            expires_at=now() + timedelta(minutes=10),
        )
    )
    db.commit()
    params = dict(
        response_type="code",
        client_id=cfg.salesforce_client_id,
        redirect_uri=callback_url(cfg),
        scope="api refresh_token",
        state=state,
        code_challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("="),
        code_challenge_method="S256",
    )
    return {
        "authorization_url": cfg.salesforce_login_url.rstrip("/")
        + "/services/oauth2/authorize?"
        + urlencode(params)
    }


@router.get("/callback", include_in_schema=False)
def callback(
    request: Request,
    state: str = Query(max_length=200),
    code: str | None = Query(None, max_length=4000),
    error: str | None = Query(None, max_length=200),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    cfg = request.app.state.settings
    if not cfg.salesforce_enabled:
        raise HTTPException(503, "Salesforce desativado.")
    record = db.get(SalesforceState, digest(state))
    if (
        not record
        or record.user_id != user.id
        or record.session_id != request.state.auth_token.id
        or record.expires_at <= now()
    ):
        raise HTTPException(400, "Autorização inválida ou expirada.")
    verifier = decrypt(cfg, record.verifier_encrypted)
    consumed = db.execute(
        delete(SalesforceState).where(
            SalesforceState.state_hash == digest(state), SalesforceState.expires_at > now()
        )
    )
    if consumed.rowcount != 1:
        db.rollback()
        raise HTTPException(400, "Autorização já utilizada.")
    db.commit()
    if error or not code:
        return RedirectResponse("/?salesforce=cancelled", status_code=303)
    try:
        result = exchange(
            cfg,
            dict(
                grant_type="authorization_code",
                code=code,
                code_verifier=verifier,
                redirect_uri=callback_url(cfg),
            ),
        )
        origin = validate_salesforce_origin(result["instance_url"])
        connection = db.get(SalesforceConnection, user.id)
        if not connection:
            connection = SalesforceConnection(user_id=user.id)
            db.add(connection)
        connection.instance_url = origin
        connection.access_encrypted = encrypt(cfg, result["access_token"])
        connection.refresh_encrypted = encrypt(cfg, result["refresh_token"])
        db.add(
            AuditEvent(
                actor_id=user.id, action="salesforce.connected", details={"instance_url": origin}
            )
        )
        db.commit()
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        db.rollback()
        return RedirectResponse("/?salesforce=failed", status_code=303)
    return RedirectResponse("/?salesforce=connected", status_code=303)


def query(cfg, db, user_id, soql):
    if not cfg.salesforce_enabled:
        raise HTTPException(503, "Salesforce desativado.")
    connection = db.get(SalesforceConnection, user_id)
    if not connection:
        raise HTTPException(409, "Conecte sua conta Salesforce em Configurações.")
    try:
        origin = validate_salesforce_origin(connection.instance_url)
        with httpx.Client(timeout=20, trust_env=False) as client:

            def fetch():
                return client.get(
                    origin + "/services/data/v66.0/query",
                    params={"q": soql},
                    headers={
                        "Authorization": "Bearer " + decrypt(cfg, connection.access_encrypted)
                    },
                )

            response = fetch()
            if response.status_code == 401:
                result = exchange(
                    cfg,
                    dict(
                        grant_type="refresh_token",
                        refresh_token=decrypt(cfg, connection.refresh_encrypted),
                    ),
                )
                if (
                    result.get("instance_url")
                    and validate_salesforce_origin(result["instance_url"]) != origin
                ):
                    raise ValueError("Instance changed; reconnect")
                connection.access_encrypted = encrypt(cfg, result["access_token"])
                if result.get("refresh_token"):
                    connection.refresh_encrypted = encrypt(cfg, result["refresh_token"])
                db.commit()
                response = fetch()
            response.raise_for_status()
            return connection, response.json()["records"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise HTTPException(
            502,
            "Não foi possível consultar o Salesforce. Verifique acesso à API ou reconecte a conta.",
        ) from None


@router.get("/opportunities")
def opportunities(
    request: Request,
    q: str = Query("", max_length=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "editor")),
):
    # Escape SOQL literals and LIKE metacharacters independently of URL encoding.
    term = q.replace("\\", "\\\\").replace("'", "\\'").replace("%", "\\%").replace("_", "\\_")
    _, records = query(
        request.app.state.settings,
        db,
        user.id,
        f"SELECT {FIELDS} FROM Opportunity WHERE Name LIKE '%{term}%' ORDER BY LastModifiedDate DESC LIMIT 25",
    )
    return {
        "items": [
            {
                "id": r["Id"],
                "name": r["Name"],
                "account": (r.get("Account") or {}).get("Name", ""),
                "stage": r.get("StageName"),
                "amount": r.get("Amount"),
                "close_date": r.get("CloseDate"),
            }
            for r in records
        ]
    }


class LinkInput(BaseModel):
    opportunity_id: str = Field(pattern=r"^006[A-Za-z0-9]{12}([A-Za-z0-9]{3})?$")


@router.get("/contracts/{contract_id}")
def get_link(contract_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)):
    link = db.get(SalesforceLink, contract_id)
    return {
        "link": {
            "opportunity_id": link.opportunity_id,
            "name": link.opportunity_name,
            "url": link.instance_url + "/" + link.opportunity_id,
        }
        if link
        else None
    }


@router.post("/contracts/{contract_id}")
def link_contract(
    contract_id: str,
    payload: LinkInput,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "editor")),
):
    contract = db.get(Contract, contract_id)
    if not contract:
        raise HTTPException(404, "Contrato não encontrado.")
    if user.role != "admin" and contract.owner_id != user.id:
        raise HTTPException(403, "Somente o autor ou administrador pode vincular este contrato.")
    connection, records = query(
        request.app.state.settings,
        db,
        user.id,
        f"SELECT {FIELDS} FROM Opportunity WHERE Id = '{payload.opportunity_id}' LIMIT 1",
    )
    if not records:
        raise HTTPException(404, "Oportunidade não encontrada ou sem permissão.")
    link = db.get(SalesforceLink, contract_id)
    if not link:
        link = SalesforceLink(contract_id=contract_id)
        db.add(link)
    link.instance_url = connection.instance_url
    link.opportunity_id = payload.opportunity_id
    link.opportunity_name = records[0]["Name"][:200]
    link.linked_by = user.id
    db.add(
        AuditEvent(
            actor_id=user.id,
            contract_id=contract_id,
            action="salesforce.linked",
            details={"opportunity_id": payload.opportunity_id},
        )
    )
    db.commit()
    return get_link(contract_id, db, user)


@router.delete("/connection", status_code=204)
def disconnect(db: Session = Depends(get_db), user: User = Depends(current_user)):
    db.execute(delete(SalesforceConnection).where(SalesforceConnection.user_id == user.id))
    db.execute(delete(SalesforceState).where(SalesforceState.user_id == user.id))
    db.add(AuditEvent(actor_id=user.id, action="salesforce.disconnected", details={}))
    db.commit()

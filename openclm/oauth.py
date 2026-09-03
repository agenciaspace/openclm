import secrets
from datetime import timedelta
from urllib.parse import urlencode, urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import AuditEvent, DocuSignConnection, OAuthState, SignatureRequest, User, now
from .security import current_user, digest, get_db, require_roles

router = APIRouter(prefix="/api/v1/integrations/docusign", tags=["DocuSign OAuth"])


def callback_url(cfg):
    return cfg.app_url.rstrip("/") + "/api/v1/integrations/docusign/callback"


def encrypt(cfg, value):
    return Fernet(cfg.token_encryption_key.encode()).encrypt(value.encode()).decode()


def decrypt(cfg, value):
    try:
        return Fernet(cfg.token_encryption_key.encode()).decrypt(value.encode()).decode()
    except InvalidToken:
        raise HTTPException(
            503, "Não foi possível ler a conexão. Verifique a chave de criptografia no servidor."
        ) from None


def exchange_token(cfg, data):
    with httpx.Client(timeout=30, trust_env=False) as client:
        response = client.post(
            f"https://{cfg.docusign_auth_server}/oauth/token",
            auth=(cfg.docusign_integration_key, cfg.docusign_client_secret),
            data=data,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result.get("access_token"), str) or not result["access_token"]:
            raise ValueError("Missing access token")
        return result


def fetch_identity(cfg, access_token):
    with httpx.Client(timeout=30, trust_env=False) as client:
        response = client.get(
            f"https://{cfg.docusign_auth_server}/oauth/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        identity = response.json()
        accounts = identity["accounts"]
        account = next(
            (a for a in accounts if str(a.get("is_default")).lower() == "true"), accounts[0]
        )
        base = urlparse(account["base_uri"])
        if (
            base.scheme != "https"
            or not (base.hostname or "").endswith(".docusign.net")
            or base.username
            or base.path not in {"", "/"}
            or base.query
            or base.fragment
        ):
            raise ValueError("Invalid DocuSign base URI")
        return identity, account


def connection_for(db, user_id):
    connection = db.get(DocuSignConnection, user_id)
    if not connection:
        raise HTTPException(409, "Conecte sua conta DocuSign em Configurações antes de enviar.")
    return connection


def access_token(cfg, db, connection):
    if connection.expires_at <= now() + timedelta(minutes=2):
        try:
            result = exchange_token(
                cfg,
                {
                    "grant_type": "refresh_token",
                    "refresh_token": decrypt(cfg, connection.refresh_encrypted),
                },
            )
            connection.access_encrypted = encrypt(cfg, result["access_token"])
            if result.get("refresh_token"):
                connection.refresh_encrypted = encrypt(cfg, result["refresh_token"])
            connection.expires_at = now() + timedelta(seconds=int(result["expires_in"]))
            db.commit()
        except (httpx.HTTPError, KeyError, ValueError):
            raise HTTPException(
                409, "A autorização DocuSign expirou ou foi revogada. Conecte sua conta novamente."
            ) from None
    return decrypt(cfg, connection.access_encrypted)


@router.get("/status")
def status(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    connection = db.get(DocuSignConnection, user.id)
    return {
        "configured": request.app.state.settings.docusign_enabled,
        "connected": bool(connection),
        "account_name": connection.account_name if connection else None,
        "account_id": connection.account_id if connection else None,
    }


@router.post("/connect")
def connect(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("admin", "editor")),
):
    cfg = request.app.state.settings
    if not cfg.docusign_enabled:
        raise HTTPException(
            503, "O administrador precisa configurar o aplicativo DocuSign nesta instalação."
        )
    if request.state.auth_token.kind != "session":
        raise HTTPException(403, "Conecte o DocuSign a partir de uma sessão no navegador.")
    raw_state = secrets.token_urlsafe(40)
    db.execute(delete(OAuthState).where(OAuthState.expires_at < now()))
    db.add(
        OAuthState(
            state_hash=digest(raw_state),
            user_id=user.id,
            session_id=request.state.auth_token.id,
            expires_at=now() + timedelta(minutes=10),
        )
    )
    db.commit()
    params = {
        "response_type": "code",
        "scope": "signature extended",
        "client_id": cfg.docusign_integration_key,
        "redirect_uri": callback_url(cfg),
        "state": raw_state,
    }
    return {
        "authorization_url": f"https://{cfg.docusign_auth_server}/oauth/auth?{urlencode(params)}"
    }


@router.get("/callback", include_in_schema=False)
def callback(
    request: Request,
    state: str = Query(max_length=200),
    code: str | None = Query(default=None, max_length=4000),
    error: str | None = Query(default=None, max_length=200),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    cfg = request.app.state.settings
    if not cfg.docusign_enabled:
        raise HTTPException(503, "DocuSign desativado.")
    # Consume atomically and bind both user and the initiating browser session.
    consumed = db.execute(
        delete(OAuthState).where(
            OAuthState.state_hash == digest(state),
            OAuthState.user_id == user.id,
            OAuthState.session_id == request.state.auth_token.id,
            OAuthState.expires_at > now(),
        )
    )
    if consumed.rowcount != 1:
        db.rollback()
        raise HTTPException(400, "Autorização inválida ou expirada. Inicie a conexão novamente.")
    db.commit()
    if error or not code:
        return RedirectResponse("/?docusign=cancelled", status_code=303)
    try:
        result = exchange_token(
            cfg,
            {"grant_type": "authorization_code", "code": code, "redirect_uri": callback_url(cfg)},
        )
        identity, account = fetch_identity(cfg, result["access_token"])
        connection = db.get(DocuSignConnection, user.id)
        pending = db.scalar(
            select(SignatureRequest.id)
            .where(
                SignatureRequest.sender_id == user.id,
                SignatureRequest.status.not_in(["completed", "declined", "voided"]),
            )
            .limit(1)
        )
        if (
            connection
            and pending
            and (
                connection.provider_user_id != identity["sub"]
                or connection.account_id != account["account_id"]
            )
        ):
            return RedirectResponse("/?docusign=account_mismatch", status_code=303)
        if not connection:
            connection = DocuSignConnection(user_id=user.id)
            db.add(connection)
        connection.provider_user_id = identity["sub"]
        connection.account_id = account["account_id"]
        connection.account_name = account["account_name"]
        connection.base_uri = account["base_uri"].rstrip("/")
        connection.access_encrypted = encrypt(cfg, result["access_token"])
        connection.refresh_encrypted = encrypt(cfg, result["refresh_token"])
        connection.expires_at = now() + timedelta(seconds=int(result["expires_in"]))
        db.add(
            AuditEvent(
                actor_id=user.id,
                action="docusign.connected",
                details={"account_id": connection.account_id},
            )
        )
        db.commit()
    except (httpx.HTTPError, KeyError, ValueError, IndexError):
        db.rollback()
        return RedirectResponse("/?docusign=failed", status_code=303)
    return RedirectResponse("/?docusign=connected", status_code=303)


@router.delete("/connection", status_code=204)
def disconnect(db: Session = Depends(get_db), user: User = Depends(current_user)):
    connection = connection_for(db, user.id)
    db.delete(connection)
    db.execute(delete(OAuthState).where(OAuthState.user_id == user.id))
    db.add(AuditEvent(actor_id=user.id, action="docusign.disconnected", details={}))
    db.commit()

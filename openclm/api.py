import json
from datetime import timedelta
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from . import schemas as s
from .documents import docx_bytes, render, snapshot, validate_answers
from .integrations import DocuSign, ask_local_ai, verify_webhook
from .models import (
    AuditEvent,
    Contract,
    DocumentVersion,
    LoginAttempt,
    SignatureRequest,
    Template,
    Token,
    User,
    WebhookEvent,
    Workflow,
    now,
)
from .oauth import access_token, connection_for
from .security import (
    current_user,
    digest,
    dummy_hash,
    get_db,
    issue_token,
    passwords,
    require_roles,
    verify_password,
)

router = APIRouter(prefix="/api/v1")
DB = Annotated[Session, Depends(get_db)]
Auth = Annotated[User, Depends(current_user)]
Admin = Annotated[User, Depends(require_roles("admin"))]
Editor = Annotated[User, Depends(require_roles("admin", "editor"))]


def audit(db, user, action, contract=None, **details):
    db.add(
        AuditEvent(
            actor_id=user.id if user else None,
            contract_id=contract.id if contract else None,
            action=action,
            details=details,
        )
    )


def get_contract(db, contract_id):
    contract = db.get(Contract, contract_id)
    if not contract:
        raise HTTPException(404, "Contrato não encontrado.")
    return contract


def check_revision(contract, revision):
    if contract.revision != revision:
        raise HTTPException(409, "O contrato foi alterado. Atualize a página antes de continuar.")


def check_owner(contract, user):
    if user.role != "admin" and contract.owner_id != user.id:
        raise HTTPException(
            403, "Somente o responsável ou um administrador pode alterar o contrato."
        )


@router.post("/auth/login", response_model=s.UserOut, tags=["Authentication"])
def login(payload: s.Login, request: Request, response: Response, db: DB):
    address_hash = digest(request.client.host if request.client else "unknown")
    cutoff = now() - timedelta(minutes=10)
    db.execute(delete(LoginAttempt).where(LoginAttempt.created_at < cutoff))
    attempts = db.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(LoginAttempt.address_hash == address_hash)
    )
    if attempts >= 10:
        raise HTTPException(
            429, "Muitas tentativas. Tente novamente em 10 minutos.", headers={"Retry-After": "600"}
        )
    user = db.scalar(select(User).where(User.email == str(payload.email).lower()))
    valid = verify_password(user.password_hash if user else dummy_hash, payload.password)
    if not user or not valid or not user.active:
        db.add(LoginAttempt(address_hash=address_hash))
        db.commit()
        raise HTTPException(401, "E-mail ou senha inválidos.")
    raw, _ = issue_token(
        db, user.id, "session", "session", timedelta(hours=request.app.state.settings.session_hours)
    )
    audit(db, user, "auth.login")
    db.commit()
    response.set_cookie(
        "openclm_session",
        raw,
        httponly=True,
        secure=request.app.state.settings.secure_cookies,
        samesite="lax",
        max_age=request.app.state.settings.session_hours * 3600,
        path="/",
    )
    return user


@router.get("/auth/me", response_model=s.UserOut, tags=["Authentication"])
def me(user: Auth):
    return user


@router.post("/auth/logout", status_code=204, tags=["Authentication"])
def logout(request: Request, response: Response, db: DB, user: Auth):
    db.delete(request.state.auth_token)
    db.commit()
    response.delete_cookie("openclm_session", path="/")


@router.get("/users", response_model=list[s.UserOut], tags=["Users"])
def users(db: DB, user: Auth):
    return db.scalars(select(User).where(User.active.is_(True)).order_by(User.name)).all()


@router.post("/users", response_model=s.UserOut, status_code=201, tags=["Users"])
def create_user(payload: s.UserCreate, db: DB, admin: Admin):
    if db.scalar(select(User).where(User.email == str(payload.email).lower())):
        raise HTTPException(409, "E-mail já cadastrado.")
    user = User(
        email=str(payload.email).lower(),
        name=payload.name,
        role=payload.role,
        password_hash=passwords.hash(payload.password),
    )
    db.add(user)
    db.flush()
    audit(db, admin, "user.created", user_id=user.id, role=user.role)
    db.commit()
    return user


@router.get("/auth/api-keys", response_model=list[s.ApiKeyOut], tags=["Authentication"])
def api_keys(db: DB, user: Auth):
    return db.scalars(
        select(Token).where(Token.user_id == user.id, Token.kind == "api", Token.expires_at > now())
    ).all()


@router.post(
    "/auth/api-keys", response_model=s.ApiKeyCreated, status_code=201, tags=["Authentication"]
)
def create_api_key(payload: s.ApiKeyCreate, db: DB, user: Auth):
    raw, token = issue_token(db, user.id, "api", payload.name, timedelta(days=payload.days))
    audit(db, user, "api_key.created", key_id=token.id)
    db.commit()
    return s.ApiKeyCreated(id=token.id, name=token.name, expires_at=token.expires_at, token=raw)


@router.delete("/auth/api-keys/{key_id}", status_code=204, tags=["Authentication"])
def revoke_api_key(key_id: str, db: DB, user: Auth):
    token = db.scalar(
        select(Token).where(Token.id == key_id, Token.user_id == user.id, Token.kind == "api")
    )
    if not token:
        raise HTTPException(404, "Chave não encontrada.")
    audit(db, user, "api_key.revoked", key_id=key_id)
    db.delete(token)
    db.commit()


@router.get("/workflows", response_model=list[s.WorkflowOut], tags=["Workflows"])
def workflows(db: DB, user: Auth):
    return db.scalars(select(Workflow).order_by(Workflow.created_at)).all()


@router.post("/workflows", response_model=s.WorkflowOut, status_code=201, tags=["Workflows"])
def create_workflow(payload: s.WorkflowCreate, db: DB, user: Admin):
    for step in payload.steps:
        if step.approver_id:
            approver = db.get(User, step.approver_id)
            if not approver or not approver.active or approver.role not in {"admin", "reviewer"}:
                raise HTTPException(
                    422, "Cada aprovador deve ser um revisor ou administrador ativo."
                )
    workflow = Workflow(**payload.model_dump())
    db.add(workflow)
    db.flush()
    audit(db, user, "workflow.created", workflow_id=workflow.id)
    db.commit()
    return workflow


@router.get("/templates", response_model=list[s.TemplateOut], tags=["Templates & Forms"])
def templates(db: DB, user: Auth):
    return db.scalars(select(Template).order_by(Template.created_at)).all()


@router.post(
    "/templates", response_model=s.TemplateOut, status_code=201, tags=["Templates & Forms"]
)
def create_template(payload: s.TemplateCreate, db: DB, user: Editor):
    if not db.get(Workflow, payload.workflow_id):
        raise HTTPException(422, "Workflow não encontrado.")
    template = Template(**payload.model_dump())
    db.add(template)
    db.flush()
    audit(db, user, "template.created", template_id=template.id)
    db.commit()
    return template


@router.get(
    "/templates/{template_id}/questions",
    response_model=list[s.Question],
    tags=["Templates & Forms"],
)
def questions(template_id: str, db: DB, user: Auth):
    template = db.get(Template, template_id)
    if not template:
        raise HTTPException(404, "Modelo não encontrado.")
    return template.questions


@router.get("/contracts", response_model=s.ContractPage, tags=["Contracts"])
def contracts(
    db: DB,
    user: Auth,
    q: str = Query(default="", max_length=200),
    status: s.Status | None = None,
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(Contract)
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(
            or_(
                Contract.title.ilike(f"%{escaped}%", escape="\\"),
                Contract.counterparty.ilike(f"%{escaped}%", escape="\\"),
            )
        )
    if status:
        stmt = stmt.where(Contract.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = db.scalars(
        stmt.order_by(Contract.created_at.desc(), Contract.id).offset(offset).limit(limit)
    ).all()
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/dashboard", tags=["Contracts"])
def dashboard(db: DB, user: Auth):
    counts = dict(db.execute(select(Contract.status, func.count()).group_by(Contract.status)).all())
    return {"total": sum(counts.values()), "by_status": counts}


@router.post("/contracts", response_model=s.ContractOut, status_code=201, tags=["Contracts"])
def create_contract(payload: s.ContractCreate, db: DB, user: Editor):
    template = db.get(Template, payload.template_id)
    if not template:
        raise HTTPException(422, "Modelo não encontrado.")
    workflow = db.get(Workflow, template.workflow_id)
    if any(step.get("approver_id") == user.id for step in workflow.steps):
        raise HTTPException(422, "O autor não pode ser um aprovador designado neste workflow.")
    answers = validate_answers(template.questions, payload.answers)
    contract = Contract(
        title=payload.title,
        counterparty=payload.counterparty,
        template_id=template.id,
        owner_id=user.id,
        answers=answers,
        content=render(template.body, answers),
        workflow_snapshot=workflow.steps,
    )
    db.add(contract)
    db.flush()
    snapshot(db, contract)
    audit(db, user, "contract.created", contract, version=1)
    db.commit()
    return contract


@router.get("/contracts/{contract_id}", response_model=s.ContractOut, tags=["Contracts"])
def contract_detail(contract_id: str, db: DB, user: Auth):
    return get_contract(db, contract_id)


@router.get("/contracts/{contract_id}/answers", tags=["Templates & Forms"])
def contract_answers(contract_id: str, db: DB, user: Auth):
    contract = get_contract(db, contract_id)
    return {
        "contract_id": contract.id,
        "template_id": contract.template_id,
        "version": contract.version,
        "answers": contract.answers,
    }


@router.patch("/contracts/{contract_id}", response_model=s.ContractOut, tags=["Contracts"])
def edit_contract(contract_id: str, payload: s.ContractEdit, db: DB, user: Editor):
    contract = get_contract(db, contract_id)
    check_owner(contract, user)
    check_revision(contract, payload.revision)
    if contract.status not in {"draft", "rejected"}:
        raise HTTPException(409, "Só é possível editar rascunhos ou contratos devolvidos.")
    template = db.get(Template, contract.template_id)
    contract.answers = validate_answers(template.questions, payload.answers)
    contract.content = render(template.body, contract.answers)
    contract.title, contract.counterparty = payload.title, payload.counterparty
    contract.version += 1
    contract.status, contract.current_step = "draft", 0
    snapshot(db, contract)
    audit(db, user, "contract.updated", contract, version=contract.version)
    db.commit()
    return contract


@router.post(
    "/contracts/{contract_id}/transitions", response_model=s.ContractOut, tags=["Workflows"]
)
def transition(contract_id: str, payload: s.Transition, db: DB, user: Auth):
    contract = get_contract(db, contract_id)
    check_revision(contract, payload.revision)
    if payload.action == "submit":
        check_owner(contract, user)
        if user.role not in {"admin", "editor"}:
            raise HTTPException(403, "Perfil sem permissão de envio.")
        if contract.status != "draft":
            raise HTTPException(409, "Somente rascunhos podem entrar em aprovação.")
        contract.status, contract.current_step = "in_review", 0
    elif payload.action in {"approve", "reject"}:
        if contract.status != "in_review":
            raise HTTPException(409, "O contrato não está em aprovação.")
        step = contract.workflow_snapshot[contract.current_step]
        if (
            user.role not in {"admin", "reviewer"}
            or user.id == contract.owner_id
            or (step.get("approver_id") and step["approver_id"] != user.id)
        ):
            raise HTTPException(403, "Aprovação exige um revisor autorizado diferente do autor.")
        if payload.action == "reject":
            if not payload.comment:
                raise HTTPException(422, "Informe o motivo da devolução.")
            contract.status = "rejected"
        else:
            contract.current_step += 1
            if contract.current_step == len(contract.workflow_snapshot):
                contract.status = "approved"
    else:
        check_owner(contract, user)
        if user.role not in {"admin", "editor"}:
            raise HTTPException(403, "Perfil sem permissão de arquivamento.")
        if contract.status not in {"draft", "rejected", "approved", "signed", "signature_declined"}:
            raise HTTPException(409, "Não é possível arquivar este contrato neste estágio.")
        contract.status = "archived"
    audit(
        db,
        user,
        f"contract.{payload.action}",
        contract,
        comment=payload.comment,
        version=contract.version,
        current_step=contract.current_step,
    )
    db.commit()
    return contract


@router.get(
    "/contracts/{contract_id}/versions", response_model=list[s.VersionOut], tags=["Documents"]
)
def versions(contract_id: str, db: DB, user: Auth):
    get_contract(db, contract_id)
    return db.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.contract_id == contract_id)
        .order_by(DocumentVersion.version.desc())
    ).all()


@router.get(
    "/contracts/{contract_id}/document",
    tags=["Documents"],
    responses={
        200: {
            "content": {
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {},
                "text/plain": {},
            }
        }
    },
)
def document(
    contract_id: str,
    db: DB,
    user: Auth,
    format: Annotated[str, Query(pattern="^(docx|txt)$")] = "docx",
    version: int | None = Query(default=None, ge=1),
):
    contract = get_contract(db, contract_id)
    document = db.scalar(
        select(DocumentVersion).where(
            DocumentVersion.contract_id == contract_id,
            DocumentVersion.version == (version or contract.version),
        )
    )
    if not document:
        raise HTTPException(404, "Versão não encontrada.")
    content = (
        docx_bytes(document.title, document.content)
        if format == "docx"
        else document.content.encode()
    )
    media = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if format == "docx"
        else "text/plain; charset=utf-8"
    )
    return Response(
        content,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="contract-{contract.id}-v{document.version}.{format}"'
        },
    )


@router.get("/audit", response_model=list[s.AuditOut], tags=["Audit"])
def events(
    db: DB,
    user: Auth,
    contract_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(AuditEvent)
    if contract_id:
        get_contract(db, contract_id)
        stmt = stmt.where(AuditEvent.contract_id == contract_id)
    elif user.role != "admin":
        raise HTTPException(403, "O histórico global exige perfil de administrador.")
    return db.scalars(
        stmt.order_by(AuditEvent.created_at.desc(), AuditEvent.id).offset(offset).limit(limit)
    ).all()


@router.get("/settings/capabilities", tags=["Integrations"])
def capabilities(request: Request, user: Auth):
    cfg = request.app.state.settings
    return {
        "ai_enabled": cfg.ai_enabled,
        "ai_model": cfg.ollama_model if cfg.ai_enabled else None,
        "docusign_enabled": cfg.docusign_enabled,
        "salesforce_enabled": cfg.salesforce_enabled,
        "telemetry": False,
        "storage": "self-hosted",
        "version": "0.1.0",
    }


@router.post("/contracts/{contract_id}/ai", response_model=s.AIResponse, tags=["Local AI"])
def ai(contract_id: str, payload: s.AIRequest, request: Request, db: DB, user: Auth):
    contract = get_contract(db, contract_id)
    cfg = request.app.state.settings
    result = ask_local_ai(cfg, contract.content, payload.task)
    audit(
        db,
        user,
        "ai.requested",
        contract,
        task=payload.task,
        model=cfg.ollama_model,
        version=contract.version,
    )
    db.commit()
    return s.AIResponse(text=result, model=cfg.ollama_model, document_version=contract.version)


@router.post(
    "/contracts/{contract_id}/signature",
    response_model=s.SignatureOut,
    status_code=201,
    tags=["DocuSign"],
)
def send_signature(
    contract_id: str, payload: s.SignRequest, request: Request, db: DB, user: Editor
):
    cfg = request.app.state.settings
    if not cfg.docusign_enabled:
        raise HTTPException(503, "DocuSign desativado nesta instalação.")
    if not cfg.app_url.startswith("https://"):
        raise HTTPException(
            409, "Configure APP_URL com HTTPS público para receber os eventos de assinatura."
        )
    contract = get_contract(db, contract_id)
    check_owner(contract, user)
    check_revision(contract, payload.revision)
    if contract.status != "approved":
        raise HTTPException(409, "A assinatura exige todas as aprovações concluídas.")
    if db.scalar(select(SignatureRequest).where(SignatureRequest.contract_id == contract_id)):
        raise HTTPException(
            409, "Já existe uma solicitação. Consulte ou reconcilie o envelope existente."
        )
    connection = connection_for(db, user.id)
    access_token(cfg, db, connection)  # Check/refresh credentials before reserving the send.
    signature = SignatureRequest(
        contract_id=contract.id,
        sender_id=user.id,
        account_id=connection.account_id,
        document_version=contract.version,
        signers=[v.model_dump(mode="json") for v in payload.signers],
    )
    db.add(signature)
    contract.status = "sending"
    audit(
        db,
        user,
        "signature.transfer_authorized",
        contract,
        version=contract.version,
        signer_count=len(payload.signers),
    )
    db.commit()  # Reserve before network I/O. An uncertain send must never create a duplicate.
    try:
        envelope_id = DocuSign(cfg, db, connection).send(contract, signature.id, signature.signers)
    except (httpx.HTTPError, HTTPException, KeyError, ValueError):
        signature.status = "dispatch_uncertain"
        db.commit()
        raise HTTPException(
            502,
            "Não foi possível confirmar o envio. Não reenvie: consulte o DocuSign e reconcilie o envelope pela API.",
        ) from None
    signature.envelope_id, signature.status = envelope_id, "sent"
    contract.status = "signature_pending"
    audit(db, user, "signature.sent", contract, envelope_id=envelope_id)
    db.commit()
    return signature


@router.get("/contracts/{contract_id}/signature", response_model=s.SignatureOut, tags=["DocuSign"])
def signature_status(contract_id: str, db: DB, user: Auth):
    get_contract(db, contract_id)
    signature = db.scalar(
        select(SignatureRequest).where(SignatureRequest.contract_id == contract_id)
    )
    if not signature:
        raise HTTPException(404, "Ainda não há solicitação de assinatura.")
    return signature


def apply_signature_status(db, signature, status):
    contract = get_contract(db, signature.contract_id)
    if signature.status in {"completed", "declined", "voided"}:
        return  # Do not regress terminal state on delayed/duplicate callbacks.
    mapping = {
        "completed": "signed",
        "declined": "signature_declined",
        "voided": "signature_declined",
        "sent": "signature_pending",
        "delivered": "signature_pending",
    }
    if status in mapping:
        signature.status, contract.status = status, mapping[status]
        audit(db, None, f"signature.{status}", contract, envelope_id=signature.envelope_id)


@router.post(
    "/contracts/{contract_id}/signature/reconcile", response_model=s.SignatureOut, tags=["DocuSign"]
)
def reconcile(contract_id: str, envelope_id: UUID, request: Request, db: DB, user: Admin):
    if not request.app.state.settings.docusign_enabled:
        raise HTTPException(503, "DocuSign desativado.")
    signature = db.scalar(
        select(SignatureRequest).where(SignatureRequest.contract_id == contract_id)
    )
    if not signature:
        raise HTTPException(404, "Solicitação não encontrada.")
    if signature.envelope_id and signature.envelope_id != str(envelope_id):
        raise HTTPException(409, "A solicitação já está vinculada a outro envelope.")
    try:
        connection = connection_for(db, signature.sender_id)
        if connection.account_id != signature.account_id:
            raise HTTPException(409, "Reconecte a conta DocuSign original do remetente.")
        status = DocuSign(request.app.state.settings, db, connection).inspect(
            str(envelope_id), signature.id
        )
    except (httpx.HTTPError, KeyError, ValueError):
        raise HTTPException(502, "Não foi possível consultar o envelope no DocuSign.") from None
    signature.envelope_id = str(envelope_id)
    apply_signature_status(db, signature, status)
    audit(
        db,
        user,
        "signature.reconciled",
        get_contract(db, contract_id),
        envelope_id=str(envelope_id),
    )
    db.commit()
    return signature


@router.post("/webhooks/docusign", tags=["DocuSign"])
async def docusign_webhook(request: Request, db: DB):
    cfg = request.app.state.settings
    if not cfg.docusign_enabled:
        raise HTTPException(503, "DocuSign desativado.")
    body = await request.body()
    if not verify_webhook(body, request.headers, cfg.docusign_hmac_secret):
        raise HTTPException(401, "Assinatura HMAC inválida.")
    event_digest = digest(body)
    if db.get(WebhookEvent, event_digest):
        return {"received": True, "duplicate": True}
    try:
        payload = json.loads(body)
        data = payload["data"]
        envelope_id = str(UUID(data["envelopeId"]))
        account_id = data["accountId"]
        event = payload["event"]
        if not isinstance(event, str) or not event.startswith("envelope-"):
            raise ValueError("Unsupported event")
        status = event.removeprefix("envelope-")
    except (ValueError, KeyError, TypeError):
        raise HTTPException(422, "Evento DocuSign JSON inválido.") from None
    signature = db.scalar(
        select(SignatureRequest).where(SignatureRequest.envelope_id == envelope_id)
    )
    if not signature:
        raise HTTPException(
            503, "Envelope ainda não registrado. Reenvie o evento após conciliação."
        )
    if signature.account_id != account_id:
        raise HTTPException(422, "A conta não corresponde ao envelope registrado.")
    apply_signature_status(db, signature, status)
    db.add(WebhookEvent(digest=event_digest))
    db.commit()
    return {"received": True}

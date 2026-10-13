"""OpenIntake: authenticated legal requests, deterministic routing and private collaboration."""

import base64
import binascii
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .accounts import lock_organization
from .api import audit
from .documents import validate_answers
from .models import (
    Contract,
    IntakeArticle,
    IntakeAttachment,
    IntakeMessage,
    IntakeRequest,
    IntakeService,
    Membership,
    User,
    now,
)
from .schemas import Input, Question
from .security import current_user, get_db, require_roles

router = APIRouter(prefix="/api/v1/intake", tags=["OpenIntake"])
DB = Annotated[Session, Depends(get_db)]
Auth = Annotated[User, Depends(current_user)]
Legal = Annotated[User, Depends(require_roles("admin", "reviewer"))]
Admin = Annotated[User, Depends(require_roles("admin"))]
Status = Literal["new", "in_progress", "waiting", "resolved", "cancelled"]
Priority = Literal["normal", "high", "urgent"]


def legal(user):
    return user.role in {"admin", "reviewer"}


def staff(db, organization_id, user_id):
    if not user_id:
        return
    membership = db.get(Membership, (organization_id, user_id))
    person = db.get(User, user_id)
    if (
        not membership
        or not membership.active
        or membership.role not in {"admin", "reviewer"}
        or not person
        or not person.active
    ):
        raise HTTPException(
            422, "Responsável deve integrar a equipe jurídica ativa desta organização."
        )


def request_query(user):
    stmt = select(IntakeRequest)
    return stmt if legal(user) else stmt.where(IntakeRequest.requester_id == user.id)


def get_request(db, user, request_id, revision=None):
    item = db.scalar(request_query(user).where(IntakeRequest.id == request_id))
    if not item:
        raise HTTPException(404, "Solicitação não encontrada.")
    if revision is not None and item.revision != revision:
        raise HTTPException(409, "A solicitação mudou. Atualize antes de continuar.")
    return item


def serialize(item):
    return {
        column.name: getattr(item, column.name)
        for column in item.__table__.columns
        if column.name != "organization_id"
    }


def service_data(item, user):
    data = serialize(item)
    if not legal(user):
        data.pop("assignee_id", None)
    return data


def question(key, label, kind="text", required=True):
    return Question(key=key, label=label, type=kind, required=required).model_dump()


def seed_services(db):
    if db.scalar(select(IntakeService.id).limit(1)):
        return
    definitions = [
        (
            "Contratos e NDA",
            "Solicite elaboração ou revisão de um contrato.",
            [
                question("counterparty", "Com quem vamos contratar?"),
                question("scope", "O que será contratado?", "textarea"),
                question("value", "Valor estimado (R$)", "number", False),
            ],
        ),
        (
            "Privacidade e dados",
            "Conte sobre um tratamento de dados, incidente ou pedido de titular.",
            [
                question("data_use", "Quais dados e pessoas estão envolvidos?", "textarea"),
                question("incident", "Há suspeita de incidente de segurança?", "boolean"),
            ],
        ),
        (
            "Pessoas e trabalho",
            "Encaminhe uma questão de RH para avaliação jurídica.",
            [question("context", "Qual é a situação e o resultado esperado?", "textarea")],
        ),
        (
            "Consulta jurídica",
            "Faça uma pergunta com o contexto necessário para análise.",
            [question("outcome", "O que você precisa decidir ou receber?", "textarea")],
        ),
    ]
    for name, description, questions in definitions:
        db.add(
            IntakeService(
                name=name,
                description=description,
                questions=questions,
                guidance="Inclua o contexto, o resultado esperado e eventuais prazos. O jurídico confirmará o atendimento após a triagem.",
            )
        )
    db.flush()


class ServiceInput(Input):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=2000)
    guidance: str = Field(default="", max_length=10000)
    questions: list[Question] = Field(default_factory=list, max_length=30)
    assignee_id: str | None = None
    sla_hours: int = Field(default=48, ge=1, le=2160)
    active: bool = True
    revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def unique_keys(self):
        keys = [q.key for q in self.questions]
        if len(keys) != len(set(keys)):
            raise ValueError("As perguntas precisam ter identificadores diferentes.")
        return self


@router.post("/setup")
def setup(db: DB, user: Admin):
    lock_organization(db, user.organization_id)
    seed_services(db)
    audit(db, user, "intake.catalog_initialized")
    db.commit()
    return {"ok": True}


@router.get("/services")
def services(db: DB, user: Auth):
    stmt = select(IntakeService).order_by(IntakeService.name)
    if user.role != "admin":
        stmt = stmt.where(IntakeService.active.is_(True))
    return [service_data(item, user) for item in db.scalars(stmt)]


@router.post("/services", status_code=201)
def create_service(payload: ServiceInput, db: DB, user: Admin):
    lock_organization(db, user.organization_id)
    staff(db, user.organization_id, payload.assignee_id)
    if db.scalar(select(func.count()).select_from(IntakeService)) >= 100:
        raise HTTPException(409, "Limite de 100 serviços por organização.")
    item = IntakeService(**payload.model_dump(exclude={"revision"}))
    db.add(item)
    audit(db, user, "intake.service_created")
    db.commit()
    return service_data(item, user)


@router.put("/services/{service_id}")
def update_service(service_id: str, payload: ServiceInput, db: DB, user: Admin):
    lock_organization(db, user.organization_id)
    item = db.get(IntakeService, service_id)
    if not item:
        raise HTTPException(404, "Serviço não encontrado.")
    if payload.revision != item.revision:
        raise HTTPException(409, "O serviço mudou. Atualize a página.")
    staff(db, user.organization_id, payload.assignee_id)
    for key, value in payload.model_dump(exclude={"revision"}).items():
        setattr(item, key, value)
    audit(db, user, "intake.service_updated", service_id=item.id)
    db.commit()
    return service_data(item, user)


@router.get("/team")
def team(db: DB, user: Legal):
    return [
        {"id": person.id, "name": person.name}
        for person in db.scalars(
            select(User)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.organization_id == user.organization_id,
                Membership.active.is_(True),
                Membership.role.in_(["admin", "reviewer"]),
                User.active.is_(True),
            )
            .order_by(User.name)
        )
    ]


class RequestInput(Input):
    service_id: str
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=10, max_length=20000)
    department: str = Field(default="", max_length=120)
    priority: Priority = "normal"
    requested_deadline: datetime | None = None
    answers: dict = Field(default_factory=dict)
    submission_key: UUID

    @model_validator(mode="after")
    def future_deadline(self):
        if self.requested_deadline:
            self.requested_deadline = (
                self.requested_deadline.replace(tzinfo=UTC)
                if not self.requested_deadline.tzinfo
                else self.requested_deadline.astimezone(UTC)
            )
            self.requested_deadline = self.requested_deadline.replace(tzinfo=None)
            if self.requested_deadline < now():
                raise ValueError("Informe um prazo futuro.")
        return self


@router.post("/requests", status_code=201)
def create_request(payload: RequestInput, db: DB, user: Auth):
    lock_organization(db, user.organization_id)
    existing = db.scalar(
        select(IntakeRequest).where(
            IntakeRequest.requester_id == user.id,
            IntakeRequest.submission_key == str(payload.submission_key),
        )
    )
    if existing:
        return serialize(existing)
    if db.scalar(select(func.count()).select_from(IntakeRequest)) >= 10000:
        raise HTTPException(409, "Limite de solicitações da organização atingido.")
    if (
        db.scalar(
            select(func.count())
            .select_from(IntakeRequest)
            .where(
                IntakeRequest.requester_id == user.id,
                IntakeRequest.created_at > now() - timedelta(hours=1),
            )
        )
        >= 30
    ):
        raise HTTPException(429, "Limite de 30 solicitações por hora. Tente mais tarde.")
    service = db.get(IntakeService, payload.service_id)
    if not service or not service.active:
        raise HTTPException(422, "Serviço indisponível.")
    answers = validate_answers(service.questions, payload.answers)
    assignee = service.assignee_id
    if assignee:
        try:
            staff(db, user.organization_id, assignee)
        except HTTPException:
            assignee = None  # Revoked/demoted owners return work to the legal queue.
    item = IntakeRequest(
        **payload.model_dump(exclude={"submission_key", "answers"}),
        submission_key=str(payload.submission_key),
        requester_id=user.id,
        assignee_id=assignee,
        answers=answers,
        service_name=service.name,
        questions_snapshot=service.questions,
        due_at=now() + timedelta(hours=service.sla_hours),
    )
    db.add(item)
    db.flush()
    audit(db, user, "intake.request_created", request_id=item.id)
    db.commit()
    return serialize(item)


@router.get("/requests")
def requests(
    db: DB,
    user: Auth,
    q: str = Query(default="", max_length=200),
    status: Status | None = None,
    mine: bool = False,
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    stmt = request_query(user)
    if status:
        stmt = stmt.where(IntakeRequest.status == status)
    if mine:
        stmt = stmt.where(IntakeRequest.requester_id == user.id)
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(
            or_(
                IntakeRequest.title.ilike(f"%{escaped}%", escape="\\"),
                IntakeRequest.department.ilike(f"%{escaped}%", escape="\\"),
            )
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(
        stmt.order_by(IntakeRequest.created_at.desc(), IntakeRequest.id).offset(offset).limit(limit)
    )
    return {
        "items": [serialize(item) for item in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/metrics")
def metrics(db: DB, user: Auth):
    sub = request_query(user).subquery()
    counts = dict(db.execute(select(sub.c.status, func.count()).group_by(sub.c.status)).all())
    overdue = db.scalar(
        select(func.count())
        .select_from(sub)
        .where(sub.c.due_at < now(), sub.c.status.in_(["new", "in_progress", "waiting"]))
    )
    unassigned = db.scalar(
        select(func.count())
        .select_from(sub)
        .where(sub.c.assignee_id.is_(None), sub.c.status.in_(["new", "in_progress", "waiting"]))
    )
    return {
        "total": sum(counts.values()),
        "by_status": counts,
        "overdue": overdue,
        "unassigned": unassigned,
    }


@router.get("/requests/{request_id}")
def detail(request_id: str, db: DB, user: Auth):
    item = get_request(db, user, request_id)
    messages = select(IntakeMessage).where(IntakeMessage.request_id == item.id)
    if not legal(user):
        messages = messages.where(IntakeMessage.internal.is_(False))
    result = serialize(item)
    result["messages"] = [
        {**serialize(m), "author_name": db.get(User, m.author_id).name}
        for m in db.scalars(messages.order_by(IntakeMessage.created_at, IntakeMessage.id))
    ]
    result["attachments"] = [
        {k: v for k, v in serialize(a).items() if k != "content_base64"}
        for a in db.scalars(
            select(IntakeAttachment)
            .where(IntakeAttachment.request_id == item.id)
            .order_by(IntakeAttachment.created_at)
        )
    ]
    result["requester_name"] = db.get(User, item.requester_id).name
    result["assignee_name"] = db.get(User, item.assignee_id).name if item.assignee_id else None
    return result


class TriageInput(Input):
    revision: int = Field(ge=1)
    status: Status
    assignee_id: str | None
    priority: Priority
    resolution: str = Field(default="", max_length=20000)


@router.patch("/requests/{request_id}")
def triage(request_id: str, payload: TriageInput, db: DB, user: Legal):
    lock_organization(db, user.organization_id)
    item = get_request(db, user, request_id, payload.revision)
    staff(db, user.organization_id, payload.assignee_id)
    if payload.status == "resolved" and not payload.resolution:
        raise HTTPException(422, "Informe a resposta final para concluir.")
    if payload.status in {"in_progress", "waiting", "resolved"} and not payload.assignee_id:
        raise HTTPException(422, "Defina um responsável para o atendimento.")
    previous = item.status
    for key, value in payload.model_dump(exclude={"revision"}).items():
        setattr(item, key, value)
    item.updated_at = now()
    item.resolved_at = (item.resolved_at or now()) if item.status == "resolved" else None
    db.add(
        IntakeMessage(
            request_id=item.id,
            author_id=user.id,
            body=f"Status: {item.status}."
            + ("\n" + item.resolution if item.status == "resolved" else ""),
        )
    )
    audit(db, user, "intake.triaged", request_id=item.id, previous=previous, status=item.status)
    db.commit()
    return serialize(item)


class MessageInput(Input):
    body: str = Field(min_length=1, max_length=10000)
    internal: bool = False


@router.post("/requests/{request_id}/messages", status_code=201)
def message(request_id: str, payload: MessageInput, db: DB, user: Auth):
    lock_organization(db, user.organization_id)
    item = get_request(db, user, request_id)
    if payload.internal and not legal(user):
        raise HTTPException(403, "Notas internas são exclusivas do jurídico.")
    if item.status == "cancelled":
        raise HTTPException(409, "Solicitação cancelada.")
    if (
        db.scalar(
            select(func.count())
            .select_from(IntakeMessage)
            .where(IntakeMessage.request_id == item.id)
        )
        >= 500
    ):
        raise HTTPException(409, "Limite de mensagens da solicitação atingido.")
    msg = IntakeMessage(request_id=item.id, author_id=user.id, **payload.model_dump())
    db.add(msg)
    if item.requester_id == user.id and item.status == "waiting" and not payload.internal:
        item.status = "new"
    item.updated_at = now()
    audit(db, user, "intake.message_added", request_id=item.id, internal=payload.internal)
    db.commit()
    return {"id": msg.id}


class AttachmentInput(Input):
    filename: str = Field(min_length=1, max_length=200, pattern=r"^[^/\\\r\n]+$")
    content_base64: str = Field(max_length=1400000)


@router.post("/requests/{request_id}/attachments", status_code=201)
def attach(request_id: str, payload: AttachmentInput, db: DB, user: Auth):
    lock_organization(db, user.organization_id)
    item = get_request(db, user, request_id)
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "Arquivo inválido.") from None
    if not content or len(content) > 1024 * 1024:
        raise HTTPException(422, "Envie arquivos de até 1 MB.")
    extension = payload.filename.rsplit(".", 1)[-1].lower()
    if extension not in {"pdf", "docx", "txt", "png", "jpg", "jpeg"}:
        raise HTTPException(422, "Formatos aceitos: PDF, DOCX, TXT, PNG e JPG.")
    if (
        db.scalar(
            select(func.count())
            .select_from(IntakeAttachment)
            .where(IntakeAttachment.request_id == item.id)
        )
        >= 5
    ):
        raise HTTPException(409, "Limite de cinco anexos por solicitação.")
    if (db.scalar(select(func.sum(IntakeAttachment.size))) or 0) + len(content) > 100 * 1024 * 1024:
        raise HTTPException(409, "Limite de 100 MB em anexos da organização atingido.")
    attachment = IntakeAttachment(
        request_id=item.id,
        filename=payload.filename,
        content_base64=payload.content_base64,
        uploaded_by=user.id,
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    db.add(attachment)
    audit(db, user, "intake.attachment_added", request_id=item.id)
    db.commit()
    return {"id": attachment.id, "filename": attachment.filename, "size": attachment.size}


@router.get("/requests/{request_id}/attachments/{attachment_id}")
def download(request_id: str, attachment_id: str, db: DB, user: Auth):
    item = get_request(db, user, request_id)
    attachment = db.get(IntakeAttachment, attachment_id)
    if not attachment or attachment.request_id != item.id:
        raise HTTPException(404, "Anexo não encontrado.")
    return Response(
        base64.b64decode(attachment.content_base64),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(attachment.filename, safe='')}",
            "X-Content-Type-Options": "nosniff",
        },
    )


class LinkInput(Input):
    revision: int = Field(ge=1)
    contract_id: str | None = None


@router.put("/requests/{request_id}/contract")
def link_contract(request_id: str, payload: LinkInput, db: DB, user: Legal):
    lock_organization(db, user.organization_id)
    item = get_request(db, user, request_id, payload.revision)
    if payload.contract_id and not db.get(Contract, payload.contract_id):
        raise HTTPException(404, "Contrato não encontrado nesta organização.")
    item.contract_id = payload.contract_id
    item.updated_at = now()
    audit(
        db, user, "intake.contract_linked", request_id=item.id, linked_contract_id=item.contract_id
    )
    db.commit()
    return serialize(item)


class ArticleInput(Input):
    title: str = Field(min_length=3, max_length=200)
    body: str = Field(min_length=10, max_length=30000)
    published: bool = False
    revision: int | None = Field(default=None, ge=1)


@router.get("/articles")
def articles(db: DB, user: Auth):
    stmt = select(IntakeArticle).order_by(IntakeArticle.title)
    if user.role != "admin":
        stmt = stmt.where(IntakeArticle.published.is_(True))
    return [serialize(item) for item in db.scalars(stmt)]


@router.post("/articles", status_code=201)
def create_article(payload: ArticleInput, db: DB, user: Admin):
    lock_organization(db, user.organization_id)
    if db.scalar(select(func.count()).select_from(IntakeArticle)) >= 100:
        raise HTTPException(409, "Limite de 100 orientações por organização.")
    item = IntakeArticle(**payload.model_dump(exclude={"revision"}))
    db.add(item)
    audit(db, user, "intake.article_created")
    db.commit()
    return serialize(item)


@router.put("/articles/{article_id}")
def update_article(article_id: str, payload: ArticleInput, db: DB, user: Admin):
    lock_organization(db, user.organization_id)
    item = db.get(IntakeArticle, article_id)
    if not item:
        raise HTTPException(404, "Orientação não encontrada.")
    if payload.revision != item.revision:
        raise HTTPException(409, "A orientação mudou. Atualize a página.")
    for key, value in payload.model_dump(exclude={"revision"}).items():
        setattr(item, key, value)
    item.updated_at = now()
    audit(db, user, "intake.article_updated", article_id=item.id)
    db.commit()
    return serialize(item)

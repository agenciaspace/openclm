"""Self-service identities, organizations and memberships."""

import secrets
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import EmailStr, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from .mail import queue_mail
from .models import (
    AuditEvent,
    Invitation,
    LoginAttempt,
    Membership,
    OAuthState,
    Organization,
    SalesforceState,
    Token,
    User,
    now,
)
from .schemas import Input, Role
from .security import (
    client_address,
    current_user,
    digest,
    get_db,
    identity_user,
    issue_token,
    passwords,
    principal,
    require_roles,
    verify_password,
)
from .seed import seed_templates

router = APIRouter(prefix="/api/v1", tags=["Accounts & Organizations"])
DB = Annotated[Session, Depends(get_db)]
Identity = Annotated[User, Depends(identity_user)]
Member = Annotated[object, Depends(current_user)]
Admin = Annotated[object, Depends(require_roles("admin"))]


class OrganizationInput(Input):
    name: str = Field(min_length=2, max_length=120)


class Signup(Input):
    email: EmailStr
    name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=12, max_length=256)
    organization_name: str | None = Field(default=None, min_length=2, max_length=120)
    model_config = {"extra": "forbid", "str_strip_whitespace": False}


class EmailInput(Input):
    email: EmailStr


class ActionToken(Input):
    token: str = Field(min_length=20, max_length=256)


class PasswordReset(ActionToken):
    password: str = Field(min_length=12, max_length=256)
    model_config = {"extra": "forbid", "str_strip_whitespace": False}


class RecoveryReset(Input):
    email: EmailStr
    code: str = Field(min_length=20, max_length=256)
    password: str = Field(min_length=12, max_length=256)
    model_config = {"extra": "forbid", "str_strip_whitespace": False}


class PasswordConfirmation(Input):
    password: str = Field(min_length=1, max_length=256)
    model_config = {"extra": "forbid", "str_strip_whitespace": False}


class InvitationInput(EmailInput):
    role: Role = "editor"


class MemberChange(Input):
    role: Role


class TransferOwner(Input):
    user_id: str = Field(min_length=1, max_length=36)


def rate_limit(db, request, action, limit=10, seconds=3600, identity=""):
    actor = identity or client_address(request)
    db.execute(delete(LoginAttempt).where(LoginAttempt.created_at < now() - timedelta(days=7)))
    key = digest(f"{action}:{actor}")
    count = db.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            LoginAttempt.address_hash == key,
            LoginAttempt.created_at > now() - timedelta(seconds=seconds),
        )
    )
    if count >= limit:
        raise HTTPException(
            429,
            "Limite temporário atingido. Tente novamente mais tarde.",
            headers={"Retry-After": str(seconds)},
        )
    db.add(LoginAttempt(address_hash=key))
    db.commit()


def audit(db, actor_id, action, **details):
    db.add(AuditEvent(actor_id=actor_id, action=action, details=details))


def organization_out(db, organization, user_id):
    membership = db.get(Membership, (organization.id, user_id))
    return {
        "id": organization.id,
        "name": organization.name,
        "role": membership.role,
        "is_owner": organization.owner_id == user_id,
    }


def new_organization(db, user, name, cfg):
    # Serialize organization creation for this identity on both SQLite and PostgreSQL.
    db.execute(update(User).where(User.id == user.id).values(active=User.active))
    owned = db.scalar(
        select(func.count()).select_from(Organization).where(Organization.owner_id == user.id)
    )
    if owned >= cfg.max_organizations_per_user:
        raise HTTPException(409, "Limite de organizações atingido para esta conta.")
    organization = Organization(name=name.strip(), owner_id=user.id)
    db.add(organization)
    db.flush()
    db.add(Membership(organization_id=organization.id, user_id=user.id, role="admin"))
    db.flush()
    db.info["organization_id"] = organization.id
    seed_templates(db, commit=False)
    audit(db, user.id, "organization.created")
    return organization


def recovery_codes(db, user_id):
    db.execute(delete(Token).where(Token.user_id == user_id, Token.kind == "recovery"))
    codes = []
    for _ in range(6):
        code = secrets.token_hex(16)
        db.add(
            Token(
                user_id=user_id,
                kind="recovery",
                name="Recovery code",
                token_hash=digest(code),
                expires_at=now() + timedelta(days=3650),
            )
        )
        codes.append(code)
    return codes


def set_session(db, request, response, user, organization_id):
    raw, _ = issue_token(
        db,
        user.id,
        "session",
        "session",
        timedelta(hours=request.app.state.settings.session_hours),
        organization_id=organization_id,
    )
    response.set_cookie(
        "openclm_session",
        raw,
        httponly=True,
        secure=request.app.state.settings.secure_cookies,
        samesite="lax",
        max_age=request.app.state.settings.session_hours * 3600,
        path=request.app.state.settings.base_path + "/",
    )


def email_action(db, cfg, user, kind):
    if cfg.mail_transport == "disabled":
        return False
    db.execute(delete(Token).where(Token.user_id == user.id, Token.kind == kind))
    raw, token = issue_token(
        db, user.id, kind, kind, timedelta(minutes=30 if kind == "reset" else 1440)
    )
    token.organization_id = None
    action = "reset" if kind == "reset" else "verify"
    link = cfg.public_url + "/#" + action + "=" + raw
    subject = "Redefinir sua senha" if kind == "reset" else "Confirme seu e-mail"
    return queue_mail(
        db,
        cfg,
        user.email,
        "OpenCLM · " + subject,
        f"{subject} no OpenCLM:\n\n{link}\n\nSe você não solicitou esta ação, ignore este e-mail. Nunca compartilhe este link.",
    )


@router.get("/auth/options")
def auth_options(request: Request):
    cfg = request.app.state.settings
    return {
        "signup_enabled": cfg.public_signup_enabled,
        "email_enabled": cfg.mail_transport != "disabled",
        "recovery_codes": True,
        "free": True,
    }


@router.post("/auth/register", status_code=201)
def register(payload: Signup, request: Request, response: Response, db: DB):
    cfg = request.app.state.settings
    if not cfg.public_signup_enabled:
        raise HTTPException(403, "Novos cadastros estão temporariamente pausados.")
    rate_limit(db, request, "signup", 30, seconds=600)
    if not payload.name.strip() or (
        payload.organization_name is not None and not payload.organization_name.strip()
    ):
        raise HTTPException(422, "Informe seu nome e o nome da organização.")
    if db.scalar(select(User.id).where(User.email == str(payload.email).lower())):
        raise HTTPException(
            409, "Não foi possível cadastrar este e-mail. Tente entrar ou recuperar o acesso."
        )
    user = User(
        email=str(payload.email).lower(),
        name=payload.name.strip(),
        password_hash=passwords.hash(payload.password),
        role="viewer",
    )
    db.add(user)
    db.flush()
    organization = (
        new_organization(db, user, payload.organization_name, cfg)
        if payload.organization_name
        else None
    )
    codes = recovery_codes(db, user.id)
    sent = email_action(db, cfg, user, "verify")
    set_session(db, request, response, user, organization.id if organization else None)
    db.commit()
    return {
        "user": principal(db, user, organization.id if organization else None),
        "recovery_codes": codes,
        "verification_queued": sent,
    }


@router.post("/auth/forgot-password", status_code=202)
def forgot_password(payload: EmailInput, request: Request, db: DB):
    rate_limit(db, request, "forgot", 5)
    user = db.scalar(
        select(User).where(User.email == str(payload.email).lower(), User.active.is_(True))
    )
    if user:
        email_action(db, request.app.state.settings, user, "reset")
    db.commit()
    return {
        "message": "Se houver uma conta com esse e-mail e envio configurado, você receberá as instruções. Também é possível usar um código de recuperação."
    }


def consume_action(db, raw, kind):
    token = db.execute(
        delete(Token)
        .where(Token.token_hash == digest(raw), Token.kind == kind, Token.expires_at > now())
        .returning(Token.user_id)
    ).scalar_one_or_none()
    user = db.get(User, token) if token else None
    if not user or not user.active:
        raise HTTPException(400, "Código inválido, utilizado ou expirado.")
    return user


def reset_password(db, user, password):
    user.password_hash = passwords.hash(password)
    db.execute(delete(Token).where(Token.user_id == user.id))
    codes = recovery_codes(db, user.id)
    db.commit()
    return {"message": "Senha atualizada. Entre novamente.", "recovery_codes": codes}


@router.post("/auth/reset-password")
def reset_with_email(payload: PasswordReset, request: Request, db: DB):
    rate_limit(db, request, "reset", 10)
    user = consume_action(db, payload.token, "reset")
    user.email_verified = True
    return reset_password(db, user, payload.password)


@router.post("/auth/recover")
def reset_with_code(payload: RecoveryReset, request: Request, db: DB):
    rate_limit(db, request, "recover", 10)
    user = consume_action(db, payload.code.strip(), "recovery")
    if user.email != str(payload.email).lower():
        raise HTTPException(400, "Código inválido, utilizado ou expirado.")
    return reset_password(db, user, payload.password)


@router.post("/auth/verify-email")
def verify_email(payload: ActionToken, request: Request, db: DB):
    rate_limit(db, request, "verify", 10)
    user = consume_action(db, payload.token, "verify")
    user.email_verified = True
    db.commit()
    return {"message": "E-mail confirmado."}


@router.post("/auth/resend-verification", status_code=202)
def resend_verification(request: Request, db: DB, user: Identity):
    rate_limit(db, request, "resend", 3, identity=user.id)
    if not user.email_verified:
        if not email_action(db, request.app.state.settings, user, "verify"):
            raise HTTPException(
                503,
                "Envio de e-mail indisponível. Seus códigos de recuperação continuam funcionando.",
            )
    db.commit()
    return {"message": "Confira sua caixa de entrada."}


@router.post("/auth/recovery-codes")
def rotate_recovery(payload: PasswordConfirmation, request: Request, db: DB, user: Identity):
    rate_limit(db, request, "codes", 5, identity=user.id)
    if not verify_password(user.password_hash, payload.password):
        raise HTTPException(403, "Senha atual incorreta.")
    codes = recovery_codes(db, user.id)
    db.commit()
    return {"recovery_codes": codes}


@router.get("/organizations")
def organizations(db: DB, user: Identity):
    rows = db.scalars(
        select(Organization)
        .join(Membership, Membership.organization_id == Organization.id)
        .where(Membership.user_id == user.id, Membership.active.is_(True))
        .order_by(Organization.name)
    ).all()
    return [organization_out(db, org, user.id) for org in rows]


@router.post("/organizations", status_code=201)
def create_organization(payload: OrganizationInput, request: Request, db: DB, user: Identity):
    if request.state.auth_token.kind != "session":
        raise HTTPException(403, "Use uma sessão no navegador para criar organizações.")
    rate_limit(db, request, "organization", 5, identity=user.id)
    organization = new_organization(db, user, payload.name, request.app.state.settings)
    db.commit()
    return organization_out(db, organization, user.id)


@router.post("/organizations/{organization_id}/activate")
def activate_organization(organization_id: str, request: Request, db: DB, user: Identity):
    if request.state.auth_token.kind != "session":
        raise HTTPException(403, "Chaves de API permanecem vinculadas à organização original.")
    member = db.get(Membership, (organization_id, user.id))
    if not member or not member.active:
        raise HTTPException(404, "Organização não encontrada.")
    token = request.state.auth_token
    db.info["organization_id"] = token.organization_id
    for model in (OAuthState, SalesforceState):
        db.execute(delete(model).where(model.session_id == token.id))
    token.organization_id = organization_id
    db.commit()
    return principal(db, user, organization_id)


@router.patch("/organizations/current")
def rename_organization(payload: OrganizationInput, db: DB, user: Admin):
    org = lock_organization(db, user.organization_id)
    org.name = payload.name
    audit(db, user.id, "organization.renamed")
    db.commit()
    return organization_out(db, org, user.id)


@router.get("/organizations/current/members")
def members(db: DB, user: Admin):
    rows = db.scalars(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.organization_id == user.organization_id, Membership.active.is_(True))
        .order_by(User.name)
    ).all()
    return [principal(db, row, user.organization_id) for row in rows]


def lock_organization(db, organization_id):
    db.execute(
        update(Organization)
        .where(Organization.id == organization_id)
        .values(revision=Organization.revision + 1)
    )
    actor_id = db.info.get("actor_id")
    if actor_id:
        member = db.get(Membership, (organization_id, actor_id), populate_existing=True)
        if not member or not member.active or member.role != db.info.get("actor_role"):
            raise HTTPException(403, "Suas permissões mudaram. Atualize a página.")
    return db.get(Organization, organization_id, populate_existing=True)


@router.patch("/organizations/current/members/{user_id}")
def update_member(user_id: str, payload: MemberChange, db: DB, user: Admin):
    org = lock_organization(db, user.organization_id)
    member = db.get(Membership, (user.organization_id, user_id))
    if not member or not member.active:
        raise HTTPException(404, "Membro não encontrado.")
    if org.owner_id == user_id and payload.role != "admin":
        raise HTTPException(
            409, "Transfira a propriedade antes de alterar o perfil do proprietário."
        )
    member.role = payload.role
    audit(db, user.id, "member.role_changed", user_id=user_id, role=payload.role)
    db.commit()
    return principal(db, db.get(User, user_id), user.organization_id)


@router.delete("/organizations/current/members/{user_id}", status_code=204)
def remove_member(user_id: str, db: DB, user: Admin):
    org = lock_organization(db, user.organization_id)
    if org.owner_id == user_id:
        raise HTTPException(409, "Transfira a propriedade antes de remover o proprietário.")
    member = db.get(Membership, (user.organization_id, user_id))
    if not member or not member.active:
        raise HTTPException(404, "Membro não encontrado.")
    member.active = False
    db.execute(
        delete(Token).where(Token.user_id == user_id, Token.organization_id == user.organization_id)
    )
    # Invalidate integration authorizations when membership is removed.
    from .models import DocuSignConnection, SalesforceConnection

    for model in (DocuSignConnection, SalesforceConnection, OAuthState, SalesforceState):
        db.execute(delete(model).where(model.user_id == user_id))
    audit(db, user.id, "member.removed", user_id=user_id)
    db.commit()


@router.post("/organizations/current/transfer-ownership")
def transfer_ownership(payload: TransferOwner, db: DB, user: Admin):
    org = lock_organization(db, user.organization_id)
    if org.owner_id != user.id:
        raise HTTPException(403, "Somente o proprietário pode transferir a organização.")
    member = db.get(Membership, (user.organization_id, payload.user_id))
    if not member or not member.active:
        raise HTTPException(404, "Membro não encontrado.")
    member.role = "admin"
    org.owner_id = payload.user_id
    audit(db, user.id, "organization.ownership_transferred", user_id=payload.user_id)
    db.commit()
    return organization_out(db, org, user.id)


@router.post("/organizations/current/invitations", status_code=201)
def invite(payload: InvitationInput, request: Request, db: DB, user: Admin):
    rate_limit(db, request, "invite", 20, identity=user.organization_id)
    cfg = request.app.state.settings
    lock_organization(db, user.organization_id)
    member_count = db.scalar(
        select(func.count())
        .select_from(Membership)
        .where(Membership.organization_id == user.organization_id, Membership.active.is_(True))
    )
    if member_count >= cfg.max_members_per_organization:
        raise HTTPException(409, "Limite de membros atingido.")
    email = str(payload.email).lower()
    existing = db.scalar(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(
            User.email == email,
            Membership.organization_id == user.organization_id,
            Membership.active.is_(True),
        )
    )
    if existing:
        raise HTTPException(409, "Essa pessoa já participa da organização.")
    db.execute(
        update(Invitation)
        .where(Invitation.email == email, Invitation.consumed_at.is_(None))
        .values(consumed_at=now())
    )
    raw = secrets.token_urlsafe(40)
    invitation = Invitation(
        email=email,
        role=payload.role,
        invited_by=user.id,
        token_hash=digest(raw),
        expires_at=now() + timedelta(days=7),
    )
    db.add(invitation)
    db.flush()
    link = cfg.public_url + "/#invite=" + raw
    organization = db.get(Organization, user.organization_id)
    queued = queue_mail(
        db,
        cfg,
        email,
        f"Convite para {organization.name} · OpenCLM",
        f"Você foi convidado para {organization.name}.\n\nAceite em até 7 dias:\n{link}\n\nO convite é pessoal e só pode ser usado uma vez.",
    )
    audit(db, user.id, "invitation.created", invitation_id=invitation.id, role=payload.role)
    db.commit()
    return {
        "id": invitation.id,
        "email": email,
        "role": payload.role,
        "expires_at": invitation.expires_at,
        "link": link,
        "email_queued": queued,
    }


@router.get("/organizations/current/invitations")
def invitations(db: DB, user: Admin):
    rows = db.scalars(
        select(Invitation)
        .where(Invitation.consumed_at.is_(None), Invitation.expires_at > now())
        .order_by(Invitation.created_at.desc())
        .limit(100)
    ).all()
    return [
        {"id": row.id, "email": row.email, "role": row.role, "expires_at": row.expires_at}
        for row in rows
    ]


@router.delete("/organizations/current/invitations/{invitation_id}", status_code=204)
def revoke_invitation(invitation_id: str, db: DB, user: Admin):
    lock_organization(db, user.organization_id)
    item = db.get(Invitation, invitation_id)
    if not item:
        raise HTTPException(404, "Convite não encontrado.")
    item.consumed_at = now()
    audit(db, user.id, "invitation.revoked", invitation_id=item.id)
    db.commit()


@router.post("/invitations/accept")
def accept_invitation(payload: ActionToken, request: Request, db: DB, user: Identity):
    if request.state.auth_token.kind != "session":
        raise HTTPException(403, "Entre pelo navegador para aceitar o convite.")
    rate_limit(db, request, "accept_invite", 10, identity=user.id)
    # A high-entropy bearer invitation is the sole cross-tenant lookup. It must
    # match the authenticated identity's email; never expose another recipient.
    with request.app.state.sessions() as lookup:
        item = lookup.scalar(
            select(Invitation).where(
                Invitation.token_hash == digest(payload.token),
                Invitation.email == user.email,
                Invitation.consumed_at.is_(None),
                Invitation.expires_at > now(),
            )
        )
        org_id = item.organization_id if item else None
    if not org_id:
        raise HTTPException(400, "Convite inválido, expirado ou destinado a outro e-mail.")
    db.info["organization_id"] = org_id
    org = lock_organization(db, org_id)
    count = db.scalar(
        select(func.count())
        .select_from(Membership)
        .where(Membership.organization_id == org_id, Membership.active.is_(True))
    )
    member = db.get(Membership, (org_id, user.id))
    if (
        not member or not member.active
    ) and count >= request.app.state.settings.max_members_per_organization:
        raise HTTPException(409, "A organização atingiu seu limite de membros.")
    accepted = db.execute(
        update(Invitation)
        .where(
            Invitation.token_hash == digest(payload.token),
            Invitation.email == user.email,
            Invitation.consumed_at.is_(None),
            Invitation.expires_at > now(),
        )
        .values(consumed_at=now())
        .returning(Invitation.role)
    ).scalar_one_or_none()
    if not accepted:
        raise HTTPException(400, "Convite já utilizado ou expirado.")
    if member and member.active:
        raise HTTPException(409, "Você já participa dessa organização.")
    if not member:
        member = Membership(organization_id=org_id, user_id=user.id, role=accepted)
        db.add(member)
    else:
        member.active, member.role = True, accepted
    # A manually shared invitation grants membership, not proof of mailbox control.
    audit(db, user.id, "invitation.accepted")
    db.commit()
    return organization_out(db, org, user.id)


@router.post("/invitations/preview")
def invitation_preview(payload: ActionToken, request: Request, db: DB):
    rate_limit(db, request, "invitation_preview", 30)
    with request.app.state.sessions() as lookup:
        item = lookup.scalar(
            select(Invitation).where(
                Invitation.token_hash == digest(payload.token),
                Invitation.consumed_at.is_(None),
                Invitation.expires_at > now(),
            )
        )
        if not item:
            raise HTTPException(400, "Convite inválido, revogado ou expirado.")
        org = lookup.get(Organization, item.organization_id)
        return {
            "organization_name": org.name,
            "email": item.email,
            "role": item.role,
            "expires_at": item.expires_at,
        }

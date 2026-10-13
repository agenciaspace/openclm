import hashlib
import hmac
import ipaddress
import secrets
from dataclasses import dataclass
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Membership, Organization, Token, User, now

passwords = PasswordHasher()
dummy_hash = passwords.hash(secrets.token_urlsafe(32))
bearer = HTTPBearer(auto_error=False)


def digest(value: str | bytes):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def verify_password(encoded: str, password: str):
    try:
        return passwords.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def get_db(request: Request):
    with request.app.state.sessions() as session:
        session.info["tenant_required"] = True
        yield session


def issue_token(
    db: Session, user_id: str, kind: str, name: str, duration: timedelta, organization_id=None
):
    raw = "oclm_" + secrets.token_urlsafe(40)
    token = Token(
        user_id=user_id,
        token_hash=digest(raw),
        kind=kind,
        name=name,
        expires_at=now() + duration,
        organization_id=organization_id or db.info.get("organization_id"),
    )
    db.add(token)
    db.flush()
    return raw, token


def identity_user(
    request: Request,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
):
    raw = credentials.credentials if credentials else request.cookies.get("openclm_session")
    if not raw:
        raise HTTPException(401, "Faça login para continuar.")
    token = db.scalar(
        select(Token).where(Token.token_hash == digest(raw), Token.expires_at > now())
    )
    if (
        not token
        or (credentials and token.kind != "api")
        or (not credentials and token.kind != "session")
    ):
        raise HTTPException(401, "Sessão ou chave inválida/expirada.")
    user = db.get(User, token.user_id)
    if not user or not user.active:
        raise HTTPException(401, "Usuário indisponível.")
    request.state.auth_token = token
    return user


@dataclass(frozen=True)
class Principal:
    id: str
    email: str
    name: str
    role: str
    active: bool
    email_verified: bool
    organization_id: str | None
    organization_name: str | None
    is_owner: bool = False


def principal(db, user, organization_id):
    membership = db.get(Membership, (organization_id, user.id)) if organization_id else None
    organization = (
        db.get(Organization, organization_id) if membership and membership.active else None
    )
    return Principal(
        id=user.id,
        email=user.email,
        name=user.name,
        active=user.active,
        email_verified=user.email_verified,
        role=membership.role if organization else "viewer",
        organization_id=organization.id if organization else None,
        organization_name=organization.name if organization else None,
        is_owner=bool(organization and organization.owner_id == user.id),
    )


def current_user(
    request: Request, db: Session = Depends(get_db), user: User = Depends(identity_user)
):
    result = principal(db, user, request.state.auth_token.organization_id)
    if not result.organization_id:
        raise HTTPException(403, "Escolha uma organização da qual você participa.")
    selected = request.headers.get("x-organization-id")
    if selected and selected != result.organization_id:
        raise HTTPException(409, "A organização mudou em outra aba. Atualize a página.")
    db.info["organization_id"] = result.organization_id
    db.info["actor_id"] = result.id
    db.info["actor_role"] = result.role
    return result


def require_roles(*roles):
    def dependency(user: User = Depends(current_user)):
        if user.role not in roles:
            raise HTTPException(403, "Seu perfil não permite esta operação.")
        return user

    return dependency


def client_address(request):
    """Trust client IP metadata only from the authenticated edge proxy."""
    secret = request.app.state.settings.proxy_secret
    supplied = request.headers.get("x-openclm-proxy-key", "")
    if secret and hmac.compare_digest(secret, supplied):
        try:
            return str(ipaddress.ip_address(request.headers.get("x-openclm-client-ip", "")))
        except ValueError:
            pass
    return request.client.host if request.client else "unknown"

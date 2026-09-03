import hashlib
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Token, User, now

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
        yield session


def issue_token(db: Session, user_id: str, kind: str, name: str, duration: timedelta):
    raw = "oclm_" + secrets.token_urlsafe(40)
    token = Token(
        user_id=user_id, token_hash=digest(raw), kind=kind, name=name, expires_at=now() + duration
    )
    db.add(token)
    db.flush()
    return raw, token


def current_user(
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


def require_roles(*roles):
    def dependency(user: User = Depends(current_user)):
        if user.role not in roles:
            raise HTTPException(403, "Seu perfil não permite esta operação.")
        return user

    return dependency

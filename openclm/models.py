from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, TenantOwned


def now():
    return datetime.now(UTC).replace(tzinfo=None)


def uid():
    return str(uuid4())


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    __mapper_args__ = {"version_id_col": revision}


class Membership(Base):
    __tablename__ = "memberships"
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Invitation(TenantOwned, Base):
    __tablename__ = "invitations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(20))
    invited_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class MailOutbox(Base):
    __tablename__ = "mail_outbox"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    payload_encrypted: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Token(Base):
    __tablename__ = "tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(80), default="session")
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id"), index=True)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    address_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class Workflow(TenantOwned, Base):
    __tablename__ = "workflows"
    __table_args__ = (UniqueConstraint("organization_id", "id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120))
    steps: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Template(TenantOwned, Base):
    __tablename__ = "templates"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        ForeignKeyConstraint(
            ["organization_id", "workflow_id"], ["workflows.organization_id", "workflows.id"]
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text)
    questions: Mapped[list] = mapped_column(JSON)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Contract(TenantOwned, Base):
    __tablename__ = "contracts"
    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        ForeignKeyConstraint(
            ["organization_id", "template_id"], ["templates.organization_id", "templates.id"]
        ),
        ForeignKeyConstraint(
            ["organization_id", "owner_id"], ["memberships.organization_id", "memberships.user_id"]
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    title: Mapped[str] = mapped_column(String(200))
    counterparty: Mapped[str] = mapped_column(String(200))
    template_id: Mapped[str] = mapped_column(ForeignKey("templates.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    answers: Mapped[dict] = mapped_column(JSON)
    content: Mapped[str] = mapped_column(Text)
    workflow_snapshot: Mapped[list] = mapped_column(JSON)
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    __mapper_args__ = {"version_id_col": revision}


class DocumentVersion(TenantOwned, Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("contract_id", "version"),
        ForeignKeyConstraint(
            ["organization_id", "contract_id"], ["contracts.organization_id", "contracts.id"]
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    counterparty: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    answers: Mapped[dict] = mapped_column(JSON)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class SignatureRequest(TenantOwned, Base):
    __tablename__ = "signature_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "contract_id"], ["contracts.organization_id", "contracts.id"]
        ),
        ForeignKeyConstraint(
            ["organization_id", "sender_id"], ["memberships.organization_id", "memberships.user_id"]
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id"), unique=True)
    sender_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    account_id: Mapped[str] = mapped_column(String(100))
    envelope_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    document_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="dispatching")
    signers: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AuditEvent(TenantOwned, Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "contract_id"], ["contracts.organization_id", "contracts.id"]
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    contract_id: Mapped[str | None] = mapped_column(ForeignKey("contracts.id"), index=True)
    action: Mapped[str] = mapped_column(String(80))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class OAuthState(TenantOwned, Base):
    __tablename__ = "oauth_states"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    session_id: Mapped[str] = mapped_column(ForeignKey("tokens.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class DocuSignConnection(TenantOwned, Base):
    __tablename__ = "docusign_connections"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    provider_user_id: Mapped[str] = mapped_column(String(100))
    account_id: Mapped[str] = mapped_column(String(100))
    account_name: Mapped[str] = mapped_column(String(200))
    base_uri: Mapped[str] = mapped_column(String(200))
    access_encrypted: Mapped[str] = mapped_column(Text)
    refresh_encrypted: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    __mapper_args__ = {"version_id_col": revision}


class SalesforceState(TenantOwned, Base):
    __tablename__ = "salesforce_states"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    session_id: Mapped[str] = mapped_column(ForeignKey("tokens.id", ondelete="CASCADE"))
    verifier_encrypted: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class SalesforceConnection(TenantOwned, Base):
    __tablename__ = "salesforce_connections"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    instance_url: Mapped[str] = mapped_column(String(250))
    access_encrypted: Mapped[str] = mapped_column(Text)
    refresh_encrypted: Mapped[str] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    __mapper_args__ = {"version_id_col": revision}


class SalesforceLink(TenantOwned, Base):
    __tablename__ = "salesforce_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "contract_id"], ["contracts.organization_id", "contracts.id"]
        ),
    )
    contract_id: Mapped[str] = mapped_column(ForeignKey("contracts.id"), primary_key=True)
    instance_url: Mapped[str] = mapped_column(String(250))
    opportunity_id: Mapped[str] = mapped_column(String(18))
    opportunity_name: Mapped[str] = mapped_column(String(200))
    linked_by: Mapped[str] = mapped_column(ForeignKey("users.id"))

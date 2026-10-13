from sqlalchemy import ForeignKey, String, create_engine, event, inspect, select
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
    with_loader_criteria,
)

LEGACY_ORGANIZATION_ID = "00000000-0000-4000-8000-000000000001"


class Base(DeclarativeBase):
    pass


class TenantOwned:
    organization_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("organizations.id"),
        index=True,
        default=LEGACY_ORGANIZATION_ID,
    )


class TenantSession(Session):
    def get(self, entity, ident, **kwargs):
        # Session.get can otherwise return an identity-map object without invoking
        # loader criteria. Always query tenant-owned objects in request sessions.
        if self.info.get("tenant_required") and issubclass(entity, TenantOwned):
            columns = inspect(entity).primary_key
            values = ident if isinstance(ident, tuple) else (ident,)
            if isinstance(ident, dict):
                values = tuple(ident[c.key] for c in columns)
            statement = select(entity).where(
                *(c == v for c, v in zip(columns, values, strict=True))
            )
            if kwargs.get("options"):
                statement = statement.options(*kwargs["options"])
            execution = dict(kwargs.get("execution_options") or {})
            if kwargs.get("populate_existing"):
                execution["populate_existing"] = True
            if kwargs.get("identity_token") is not None:
                execution["identity_token"] = kwargs["identity_token"]
            locking = kwargs.get("with_for_update")
            if locking is not None and locking is not False:
                statement = statement.with_for_update(
                    **(locking if isinstance(locking, dict) else {})
                )
            return self.scalar(
                statement.execution_options(**execution),
                bind_arguments=kwargs.get("bind_arguments"),
            )
        return super().get(entity, ident, **kwargs)


@event.listens_for(TenantSession, "do_orm_execute")
def tenant_queries(execute_state):
    if execute_state.session.info.get("tenant_required"):
        organization_id = execute_state.session.info.get("organization_id", "")
        execute_state.statement = execute_state.statement.options(
            with_loader_criteria(
                TenantOwned,
                lambda cls: cls.organization_id == organization_id,
                include_aliases=True,
            )
        )


@event.listens_for(TenantSession, "before_flush")
def tenant_writes(session, flush_context, instances):
    organization_id = session.info.get("organization_id")
    for obj in session.new | session.dirty | session.deleted:
        if not isinstance(obj, TenantOwned):
            continue
        if obj in session.new and organization_id and obj.organization_id is None:
            obj.organization_id = organization_id
        if session.info.get("tenant_required"):
            if not organization_id or obj.organization_id != organization_id:
                raise ValueError("Organization scope required for writes")
            if obj not in session.new and inspect(obj).attrs.organization_id.history.has_changes():
                raise ValueError("Organization ownership is immutable")


def make_engine(url: str):
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
        pool_pre_ping=True,
    )
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def sqlite_settings(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")

    return engine


def session_factory(engine):
    return sessionmaker(engine, expire_on_commit=False, class_=TenantSession)

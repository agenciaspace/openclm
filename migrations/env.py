from alembic import context

from openclm import models  # noqa: F401
from openclm.config import Settings
from openclm.db import Base, make_engine

url = Settings().database_url
if context.is_offline_mode():
    context.configure(
        url=url,
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = make_engine(url)
    with engine.connect() as connection:
        if url.startswith("sqlite"):
            # SQLite table rebuilds require FK checks outside the migration
            # transaction. Verify every relation before accepting the result.
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
        context.configure(
            connection=connection,
            target_metadata=Base.metadata,
            compare_type=True,
            render_as_batch=url.startswith("sqlite"),
        )
        with context.begin_transaction():
            context.run_migrations()
        if url.startswith("sqlite"):
            if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("Migration produced invalid foreign keys")
            connection.commit()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    engine.dispose()

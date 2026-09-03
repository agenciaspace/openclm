from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from openclm.db import make_engine


def test_clean_install_upgrade_and_downgrade(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path}/migrations.db"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    engine = make_engine(url)
    inspector = inspect(engine)
    assert {
        "users",
        "contracts",
        "document_versions",
        "oauth_states",
        "docusign_connections",
    }.issubset(inspector.get_table_names())
    assert {"sender_id", "account_id"}.issubset(
        c["name"] for c in inspector.get_columns("signature_requests")
    )
    command.check(config)
    command.downgrade(config, "base")
    assert inspect(engine).get_table_names() == ["alembic_version"]
    engine.dispose()

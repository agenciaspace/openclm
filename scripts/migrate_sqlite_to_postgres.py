"""Copy an upgraded SQLite snapshot into an EMPTY PostgreSQL database.

Set OPENCLM_MIGRATION_SOURCE and OPENCLM_MIGRATION_TARGET. Stop application writes
before taking the snapshot. Refuses other database types, schema mismatches and
non-empty targets. Reports counts and fingerprints, never document contents.
"""

import hashlib
import json
import os

from sqlalchemy import func, select, text

from openclm import models  # noqa: F401
from openclm.db import Base, make_engine


def fingerprint(rows):
    encoded = sorted(
        json.dumps(dict(row), default=str, sort_keys=True, ensure_ascii=False) for row in rows
    )
    return hashlib.sha256("\n".join(encoded).encode()).hexdigest()


def migrate(source_url, target_url):
    if not source_url.startswith("sqlite:///") or not target_url.startswith("postgresql+"):
        raise ValueError("Requires a SQLite snapshot and PostgreSQL target")
    source, target = make_engine(source_url), make_engine(target_url)
    report = {}
    try:
        with source.connect() as src, target.begin() as dst:
            if src.scalar(text("SELECT version_num FROM alembic_version")) != dst.scalar(
                text("SELECT version_num FROM alembic_version")
            ):
                raise ValueError("Upgrade both databases to the same migration first")
            tables = Base.metadata.sorted_tables
            for table in tables:
                if dst.scalar(select(func.count()).select_from(table)):
                    raise ValueError(f"Target table {table.name} is not empty")
            for table in tables:
                rows = src.execute(select(table)).mappings().all()
                if rows:
                    dst.execute(table.insert(), [dict(row) for row in rows])
                restored = dst.execute(select(table)).mappings().all()
                if fingerprint(rows) != fingerprint(restored):
                    raise RuntimeError(f"Restored fingerprint mismatch: {table.name}")
                report[table.name] = {"rows": len(rows), "sha256": fingerprint(rows)}
            dst.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence('login_attempts', 'id'), COALESCE((SELECT MAX(id) FROM login_attempts), 0) + 1, false)"
                )
            )
    finally:
        source.dispose()
        target.dispose()
    return report


if __name__ == "__main__":
    print(
        json.dumps(
            migrate(os.environ["OPENCLM_MIGRATION_SOURCE"], os.environ["OPENCLM_MIGRATION_TARGET"]),
            indent=2,
        )
    )

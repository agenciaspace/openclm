"""Exercise migrations and backup/restore using a disposable PostgreSQL 17 container.

Run: python -m scripts.postgres_readiness
Only the uniquely named container created here is removed, including on failure.
"""

import hashlib
import json
import os
import secrets
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from openclm.config import Settings
from openclm.db import make_engine
from openclm.main import create_app
from openclm.models import User
from openclm.security import passwords
from openclm.seed import seed_templates
from tests.conftest import PASSWORD, approved_contract


def run(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, **kwargs).stdout


def main():
    name = "openclm-readiness-" + uuid4().hex[:12]
    password = secrets.token_hex(24)
    report = {"engine": "PostgreSQL 17", "synthetic_data_only": True, "checks": []}
    created = False
    try:
        run(
            "docker",
            "run",
            "--detach",
            "--rm",
            "--name",
            name,
            "--publish",
            "127.0.0.1::5432",
            "--tmpfs",
            "/var/lib/postgresql/data",
            "--env",
            "POSTGRES_DB=audit",
            "--env",
            "POSTGRES_USER=audit",
            "--env",
            f"POSTGRES_PASSWORD={password}",
            "postgres:17-alpine",
        )
        created = True
        port = run("docker", "port", name, "5432/tcp").decode().strip().rsplit(":", 1)[1]
        for _ in range(30):
            result = subprocess.run(
                ["docker", "exec", name, "pg_isready", "-U", "audit", "-d", "audit"],
                capture_output=True,
            )
            if result.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Disposable PostgreSQL did not become ready")
        url = f"postgresql+psycopg://audit:{password}@127.0.0.1:{port}/audit"
        env = {**os.environ, "DATABASE_URL": url}
        run(".venv/bin/alembic", "upgrade", "head", env=env)
        run(".venv/bin/alembic", "check", env=env)
        report["checks"].append("clean_migration_and_schema_drift_check")
        run(
            ".venv/bin/pytest",
            "-q",
            "tests/test_organizations.py",
            "tests/test_account_mail.py",
            "tests/test_intake.py",
            env={**os.environ, "OPENCLM_TEST_DATABASE_URL": url},
        )
        report["checks"].append("organization_and_account_acceptance_on_isolated_postgres_schemas")
        app = create_app(
            Settings(
                _env_file=None, database_url=url, app_url="https://testserver", secure_cookies=True
            )
        )
        with app.state.sessions() as db:
            for role in ["admin", "reviewer"]:
                db.add(
                    User(
                        email=f"{role}@example.com",
                        name=role,
                        role=role,
                        password_hash=passwords.hash(PASSWORD),
                    )
                )
            db.commit()
            seed_templates(db)
        with TestClient(app, base_url="https://testserver", headers={"X-OpenCLM": "1"}) as client:
            contract = approved_contract(client)
            document = client.get(f"/api/v1/contracts/{contract['id']}/document?format=txt")
            document.raise_for_status()
            original_digest = hashlib.sha256(document.content).hexdigest()
        report["checks"].append("create_submit_independent_approval_and_download")
        backup = run("docker", "exec", name, "pg_dump", "-U", "audit", "-d", "audit", "-Fc")
        run("docker", "exec", name, "createdb", "-U", "audit", "restored")
        run(
            "docker",
            "exec",
            "-i",
            name,
            "pg_restore",
            "-U",
            "audit",
            "-d",
            "restored",
            "--exit-on-error",
            input=backup,
        )
        restored_url = url.rsplit("/", 1)[0] + "/restored"
        restored = create_app(
            Settings(
                _env_file=None,
                database_url=restored_url,
                app_url="https://testserver",
                secure_cookies=True,
            )
        )
        with TestClient(
            restored, base_url="https://testserver", headers={"X-OpenCLM": "1"}
        ) as client:
            response = client.post(
                "/api/v1/auth/login", json={"email": "admin@example.com", "password": PASSWORD}
            )
            assert response.status_code == 200
            response = client.get(f"/api/v1/contracts/{contract['id']}")
            assert response.status_code == 200 and response.json()["status"] == "approved"
            document = client.get(f"/api/v1/contracts/{contract['id']}/document?format=txt")
            assert document.status_code == 200
            assert hashlib.sha256(document.content).hexdigest() == original_digest
            assert client.get(f"/api/v1/audit?contract_id={contract['id']}").json()
        report["checks"].append("pg_dump_restore_login_approved_status_document_sha256_and_audit")
        run(".venv/bin/alembic", "downgrade", "base", env=env)
        run(".venv/bin/alembic", "upgrade", "head", env=env)
        engine = make_engine(url)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM contracts")) == 0
        engine.dispose()
        report["checks"].append("disposable_database_downgrade_and_upgrade")
        report["status"] = "passed"
        output = Path("artifacts/postgres-readiness.json")
        output.parent.mkdir(exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    finally:
        if created:
            subprocess.run(["docker", "rm", "--force", name], capture_output=True, check=True)


if __name__ == "__main__":
    main()

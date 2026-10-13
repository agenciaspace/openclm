"""Readiness inventory. Uses only a disposable database, never live credentials.

Run: python -m scripts.production_readiness --output artifacts/readiness.json
Exit 2 means public multi-organization release remains blocked; 1 is a probe error.
This is a conservative inventory, not proof of tenant isolation or provider approval.
"""

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from openclm.config import Settings
from openclm.db import Base
from openclm.main import create_app
from openclm.models import User
from openclm.security import passwords
from openclm.seed import seed_templates
from tests.conftest import PASSWORD, contract_payload, login


def audit():
    checks = []

    def record(key, status, evidence):
        checks.append({"id": key, "status": status, "evidence": evidence})

    with TemporaryDirectory(prefix="openclm-readiness-") as directory:
        cfg = Settings(
            _env_file=None,
            database_url=f"sqlite:///{directory}/audit.db",
            app_url="https://testserver",
            secure_cookies=True,
            ai_enabled=False,
            docusign_enabled=False,
            salesforce_enabled=False,
        )
        app = create_app(cfg)
        Base.metadata.create_all(app.state.engine)
        with app.state.sessions() as db:
            for role in ["admin", "viewer"]:
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
        with TestClient(app, base_url=cfg.app_url, headers={"X-OpenCLM": "1"}) as client:
            paths = client.get("/openapi.json").json()["paths"]
            # Route discovery is evidence of missing capabilities, never a positive
            # acceptance test. New routes still require real journey/isolation tests.
            requirements = {
                "public_signup": ("register", "signup", "sign-up"),
                "organizations": ("organization", "tenant", "workspace"),
                "membership_invites": ("invitation", "invite"),
                "password_recovery": ("forgot-password", "reset-password", "password/reset"),
                "email_verification": ("verify-email", "email/verify", "verification"),
            }
            for name, terms in requirements.items():
                matching = [p for p in paths if any(term in p for term in terms)]
                record(
                    name,
                    "needs_validation" if matching else "blocked",
                    {"matching_routes": matching, "method": "OpenAPI inventory"},
                )
            response = client.post(
                "/api/v1/users",
                json={
                    "email": "visitor@example.com",
                    "name": "Visitor",
                    "role": "viewer",
                    "password": PASSWORD,
                },
            )
            record(
                "existing_user_creation_requires_admin",
                "passed" if response.status_code == 401 else "blocked",
                {"anonymous_post_users": response.status_code},
            )
            login(client)
            response = client.post("/api/v1/contracts", json=contract_payload(client))
            response.raise_for_status()
            contract_id = response.json()["id"]
            login(client, "viewer")
            resources = [
                f"/contracts/{contract_id}",
                f"/contracts/{contract_id}/answers",
                f"/contracts/{contract_id}/versions",
                f"/contracts/{contract_id}/document?format=txt",
                f"/audit?contract_id={contract_id}",
            ]
            outcomes = {
                p.replace(contract_id, "{id}"): client.get("/api/v1" + p).status_code
                for p in resources
            }
            record(
                "shared_installation_visibility",
                "blocked" if 200 in outcomes.values() else "needs_validation",
                {
                    "viewer_reading_admin_contract": outcomes,
                    "interpretation": "Expected in the current single-organization model; cannot host unrelated organizations safely without tenancy.",
                },
            )
            tenant_columns = {
                name: [
                    c.name
                    for c in table.columns
                    if c.name in {"organization_id", "tenant_id", "workspace_id"}
                ]
                for name, table in Base.metadata.tables.items()
                if name in {"contracts", "templates", "workflows", "users", "docusign_connections"}
            }
            record(
                "tenant_schema",
                "needs_validation" if all(tenant_columns.values()) else "blocked",
                tenant_columns,
            )
            record(
                "membership_role_lifecycle",
                "needs_validation" if any("members" in p for p in paths) else "blocked",
                {
                    "user_management_routes": {
                        p: list(v) for p, v in paths.items() if "/users" in p
                    },
                    "cli_reset_and_disable_exist": True,
                },
            )
            record(
                "docusign_real_roundtrip",
                "needs_validation",
                {
                    "oauth_and_signature_routes": [
                        p for p in paths if "docusign" in p or "/signature" in p
                    ],
                    "note": "Mocked tests cannot establish live account eligibility, OAuth, sending, completion or webhook delivery.",
                },
            )
            document_parameters = paths["/api/v1/contracts/{contract_id}/document"]["get"][
                "parameters"
            ]
            formats = next(p["schema"] for p in document_parameters if p["name"] == "format")
            record(
                "signed_document_archive",
                "blocked" if "pdf" not in json.dumps(formats).lower() else "needs_validation",
                {
                    "format_schema": formats,
                    "note": "Document download currently offers generated DOCX/TXT; signed PDF and completion certificate require provider validation and private storage.",
                },
            )
        app.state.engine.dispose()
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "scope": "Disposable SQLite fixture; no writes or provider requests to production",
        "release_ready": all(check["status"] == "passed" for check in checks),
        "checks": checks,
        "additional_gates": [
            "Cross-organization E2E including guessed IDs, search, downloads, tokens and callbacks",
            "PostgreSQL migration, restore, load and operational monitoring",
            "Real browser onboarding and mobile accessibility",
            "Free-tier quotas, abuse controls and privacy/retention process",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/readiness.json"))
    args = parser.parse_args()
    report = audit()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    for check in report["checks"]:
        print(f"{check['status'].upper():18} {check['id']}")
    print(
        f"Report: {args.output}; public release: {'READY' if report['release_ready'] else 'BLOCKED'}"
    )
    raise SystemExit(0 if report["release_ready"] else 2)


if __name__ == "__main__":
    main()

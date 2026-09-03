from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from openclm.config import Settings
from openclm.db import Base
from openclm.main import create_app
from openclm.models import DocuSignConnection, User, now
from openclm.oauth import encrypt
from openclm.security import passwords
from openclm.seed import seed_templates

PASSWORD = "local-test-password-2026"


@pytest.fixture
def app(tmp_path):
    cfg = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path}/test.db",
        app_url="https://testserver",
        secure_cookies=True,
        token_encryption_key=Fernet.generate_key().decode(),
    )
    app = create_app(cfg)
    Base.metadata.create_all(app.state.engine)
    with app.state.sessions() as db:
        for role in ["admin", "editor", "reviewer", "viewer"]:
            db.add(
                User(
                    email=f"{role}@example.com",
                    name=role.title(),
                    role=role,
                    password_hash=passwords.hash(PASSWORD),
                )
            )
        db.commit()
        seed_templates(db)
    yield app
    app.state.engine.dispose()


@pytest.fixture
def client(app):
    with TestClient(app, base_url="https://testserver", headers={"X-OpenCLM": "1"}) as client:
        yield client


def login(client, role="admin"):
    response = client.post(
        "/api/v1/auth/login", json={"email": f"{role}@example.com", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return response.json()


def contract_payload(client):
    template = client.get("/api/v1/templates").json()[0]
    return {
        "title": "Serviços de design",
        "counterparty": "Acme Ltda",
        "template_id": template["id"],
        "answers": {
            "party_name": "OpenCLM Demo",
            "party_id": "00.000.000/0001-00",
            "counterparty_name": "Acme Ltda",
            "counterparty_id": "11.111.111/0001-11",
            "effective_date": "2026-09-03",
            "term_months": 12,
            "jurisdiction": "São Paulo",
            "scope": "Design de interfaces",
            "fee": "R$ 5.000,00",
        },
    }


def create_contract(client, role="admin"):
    login(client, role)
    response = client.post("/api/v1/contracts", json=contract_payload(client))
    assert response.status_code == 201, response.text
    return response.json()


def move(client, contract, action, **extra):
    return client.post(
        f"/api/v1/contracts/{contract['id']}/transitions",
        json={"revision": contract["revision"], "action": action, **extra},
    )


def approved_contract(client):
    contract = create_contract(client)
    contract = move(client, contract, "submit").json()
    login(client, "reviewer")
    response = move(client, contract, "approve")
    assert response.status_code == 200, response.text
    login(client)
    return response.json()


def enable_docusign(app, user_id):
    cfg = app.state.settings
    cfg.docusign_enabled = True
    cfg.docusign_integration_key = "client-id"
    cfg.docusign_client_secret = "client-secret"
    cfg.docusign_hmac_secret = "test-only-integrator-hmac"
    with app.state.sessions() as db:
        connection = DocuSignConnection(
            user_id=user_id,
            provider_user_id="provider-user",
            account_id="account-1",
            account_name="Demo Account",
            base_uri="https://demo.docusign.net",
            access_encrypted=encrypt(cfg, "private-access-token"),
            refresh_encrypted=encrypt(cfg, "private-refresh-token"),
            expires_at=now() + timedelta(hours=2),
        )
        db.add(connection)
        db.commit()
    return cfg

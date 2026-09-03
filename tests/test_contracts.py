import json
from io import BytesIO

import pytest
from docx import Document
from sqlalchemy import select
from sqlalchemy.orm.exc import StaleDataError

from openclm.models import Contract, Token

from .conftest import approved_contract, contract_payload, create_contract, login, move


@pytest.mark.parametrize("answer", [10**400, "bad\u0000text", "bad\ud800text"])
def test_extreme_or_invalid_document_values_rejected(client, answer):
    login(client)
    payload = contract_payload(client)
    payload["answers"]["term_months" if isinstance(answer, int) else "scope"] = answer
    response = client.post(
        "/api/v1/contracts",
        content=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422


def test_authentication_and_role_boundaries(client):
    for path in ["/contracts", "/templates", "/users", "/audit", "/settings/capabilities"]:
        assert client.get("/api/v1" + path).status_code == 401
    login(client, "viewer")
    assert client.post("/api/v1/contracts", json=contract_payload(client)).status_code == 403
    assert client.get("/api/v1/audit").status_code == 403
    assert (
        client.post(
            "/api/v1/users",
            json={
                "email": "new@example.com",
                "name": "New",
                "role": "admin",
                "password": "long-password-123",
            },
        ).status_code
        == 403
    )


def test_csrf_and_login_rate_limit(client):
    client.headers.pop("X-OpenCLM")
    payload = {"email": "admin@example.com", "password": "wrong-password"}
    assert client.post("/api/v1/auth/login", json=payload).status_code == 403
    client.headers["X-OpenCLM"] = "1"
    assert (
        client.post(
            "/api/v1/auth/login", json=payload, headers={"Origin": "https://attacker.example"}
        ).status_code
        == 403
    )
    for _ in range(10):
        assert client.post("/api/v1/auth/login", json=payload).status_code == 401
    assert client.post("/api/v1/auth/login", json=payload).status_code == 429


def test_api_key_revocation_and_no_secret_disclosure(client, app):
    user = login(client)
    assert "password_hash" not in user
    response = client.post("/api/v1/auth/api-keys", json={"name": "ERP", "days": 1})
    key = response.json()
    with app.state.sessions() as db:
        token = db.get(Token, key["id"])
        assert token.token_hash != key["token"]
    listing = client.get("/api/v1/auth/api-keys").json()
    assert "token" not in listing[0] and "token_hash" not in listing[0]
    headers = {"Authorization": f"Bearer {key['token']}"}
    assert client.get("/api/v1/contracts", headers=headers).status_code == 200
    assert client.delete(f"/api/v1/auth/api-keys/{key['id']}").status_code == 204
    assert client.get("/api/v1/contracts", headers=headers).status_code == 401
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/contracts").status_code == 401


@pytest.mark.parametrize(
    "change",
    [
        {"term_months": True},
        {"term_months": "12"},
        {"effective_date": "2026-02-30"},
        {"unknown": "answer"},
        {"scope": ""},
    ],
)
def test_typed_answer_validation(client, change):
    login(client)
    payload = contract_payload(client)
    payload["answers"].update(change)
    assert client.post("/api/v1/contracts", json=payload).status_code == 422


def test_generation_revision_and_immutable_versions(client):
    c = create_contract(client)
    path = f"/api/v1/contracts/{c['id']}"
    assert "Acme Ltda" in c["content"] and "{{" not in c["content"]
    downloaded = client.get(path + "/document")
    assert downloaded.status_code == 200
    doc = Document(BytesIO(downloaded.content))
    assert "Design de interfaces" in "\n".join(p.text for p in doc.paragraphs)
    edited = client.patch(
        path,
        json={
            "revision": c["revision"],
            "title": "Alterado",
            "counterparty": c["counterparty"],
            "answers": {**c["answers"], "scope": "Novo escopo"},
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["version"] == 2
    assert (
        client.patch(
            path,
            json={
                "revision": c["revision"],
                "title": "Conflito",
                "counterparty": c["counterparty"],
                "answers": c["answers"],
            },
        ).status_code
        == 409
    )
    versions = client.get(path + "/versions").json()
    assert len(versions) == 2 and versions[0]["sha256"] != versions[1]["sha256"]
    assert "Design de interfaces" in client.get(path + "/document?format=txt&version=1").text
    assert client.get(path + "/document?version=999").status_code == 404
    assert client.get(path + "/answers").json()["answers"]["scope"] == "Novo escopo"


def test_workflow_no_self_approval_and_rejection(client):
    c = create_contract(client)
    assert move(client, c, "approve").status_code == 409
    c = move(client, c, "submit").json()
    assert move(client, c, "approve").status_code == 403
    assert move(client, c, "archive").status_code == 409
    login(client, "reviewer")
    assert move(client, c, "reject").status_code == 422
    rejected = move(client, c, "reject", comment="Detalhe o escopo")
    assert rejected.json()["status"] == "rejected"
    c = rejected.json()
    login(client)
    assert move(client, c, "submit").status_code == 409
    c = client.patch(
        f"/api/v1/contracts/{c['id']}",
        json={
            "revision": c["revision"],
            "title": c["title"],
            "counterparty": c["counterparty"],
            "answers": c["answers"],
        },
    ).json()
    c = move(client, c, "submit").json()
    login(client, "reviewer")
    c = move(client, c, "approve").json()
    assert c["status"] == "approved"
    assert move(client, c, "approve").status_code == 409
    assert (
        client.patch(
            f"/api/v1/contracts/{c['id']}",
            json={
                "revision": c["revision"],
                "title": c["title"],
                "counterparty": c["counterparty"],
                "answers": c["answers"],
            },
        ).status_code
        == 403
    )


def test_ordered_designated_approvers(client):
    admin = login(client)
    reviewer = next(u for u in client.get("/api/v1/users").json() if u["role"] == "reviewer")
    workflow = client.post(
        "/api/v1/workflows",
        json={
            "name": "Duas etapas",
            "steps": [
                {"name": "Jurídico", "approver_id": reviewer["id"]},
                {"name": "Diretoria", "approver_id": admin["id"]},
            ],
        },
    ).json()
    base = client.get("/api/v1/templates").json()[0]
    template = client.post(
        "/api/v1/templates",
        json={
            k: v
            for k, v in {**base, "workflow_id": workflow["id"]}.items()
            if k not in {"id", "created_at"}
        },
    ).json()
    login(client, "editor")
    payload = contract_payload(client)
    payload["template_id"] = template["id"]
    c = client.post("/api/v1/contracts", json=payload).json()
    c = move(client, c, "submit").json()
    login(client)
    assert move(client, c, "approve").status_code == 403
    login(client, "reviewer")
    c = move(client, c, "approve").json()
    assert c["status"] == "in_review" and c["current_step"] == 1
    assert move(client, c, "approve").status_code == 403
    login(client)
    assert move(client, c, "approve").json()["status"] == "approved"


def test_ownership_search_and_readonly_approved(client):
    c = approved_contract(client)
    login(client, "editor")
    assert move(client, c, "archive").status_code == 403
    login(client)
    assert (
        client.patch(
            f"/api/v1/contracts/{c['id']}",
            json={
                "revision": c["revision"],
                "title": c["title"],
                "counterparty": c["counterparty"],
                "answers": c["answers"],
            },
        ).status_code
        == 409
    )
    assert client.get("/api/v1/contracts?q=acme&status=approved").json()["total"] == 1
    assert client.get("/api/v1/contracts?q=%25").json()["total"] == 0
    assert client.get("/api/v1/contracts?limit=101").status_code == 422
    assert move(client, c, "archive").json()["status"] == "archived"


def test_concurrent_transitions_detect_stale_write(client, app):
    c = create_contract(client)
    with app.state.sessions() as first, app.state.sessions() as second:
        a = first.get(Contract, c["id"])
        b = second.get(Contract, c["id"])
        a.status = "in_review"
        first.commit()
        b.status = "archived"
        with pytest.raises(StaleDataError):
            second.commit()
    with app.state.sessions() as db:
        assert db.scalar(select(Contract.status).where(Contract.id == c["id"])) == "in_review"


def test_local_only_assets_openapi_and_size_limit(client):
    assert client.get("/").status_code == 200
    assert "script-src 'self'" in client.get("/").headers["content-security-policy"]
    assert client.get("/docs").status_code == 200
    spec = client.get("/openapi.json").json()
    assert "/api/v1/contracts" in spec["paths"]
    assert "Question" in spec["components"]["schemas"]
    assert (
        client.post("/api/v1/auth/login", content=b"a" * (2 * 1024 * 1024 + 1)).status_code == 413
    )

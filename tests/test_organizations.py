from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from openclm.models import Contract, Invitation, Token, now

from .conftest import PASSWORD, contract_payload, create_contract, login


def signup(client, email="new@example.com"):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "name": "New user",
            "email": email,
            "password": PASSWORD,
            "organization_name": "Independent organization",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def browser(app):
    return TestClient(app, base_url="https://testserver", headers={"X-OpenCLM": "1"})


def invite(client, email, role="viewer"):
    response = client.post(
        "/api/v1/organizations/current/invitations", json={"email": email, "role": role}
    )
    assert response.status_code == 201, response.text
    return response.json()["link"].split("#invite=")[1]


def test_signup_has_private_workspace_and_cannot_read_other_organization(client, app):
    existing = create_contract(client)
    old_template = client.get("/api/v1/templates").json()[0]
    old_user = client.get("/api/v1/auth/me").json()
    with browser(app) as outsider:
        result = signup(outsider)
        assert result["user"]["organization_id"] != old_user["organization_id"]
        assert len(result["recovery_codes"]) == 6
        assert outsider.get("/api/v1/contracts").json()["total"] == 0
        assert outsider.get("/api/v1/dashboard").json()["total"] == 0
        assert len(outsider.get("/api/v1/templates").json()) == 2
        assert len(outsider.get("/api/v1/workflows").json()) == 1
        assert len(outsider.get("/api/v1/users").json()) == 1
        for path in [
            f"/contracts/{existing['id']}",
            f"/contracts/{existing['id']}/answers",
            f"/contracts/{existing['id']}/versions",
            f"/contracts/{existing['id']}/document",
            f"/contracts/{existing['id']}/signature",
            f"/audit?contract_id={existing['id']}",
            f"/templates/{old_template['id']}/questions",
            f"/integrations/salesforce/contracts/{existing['id']}",
        ]:
            assert outsider.get("/api/v1" + path).status_code == 404, path
        payload = contract_payload(outsider)
        payload["template_id"] = old_template["id"]
        assert outsider.post("/api/v1/contracts", json=payload).status_code == 422
        assert (
            outsider.post(
                "/api/v1/workflows",
                json={
                    "name": "Foreign approver",
                    "steps": [{"name": "Review", "approver_id": old_user["id"]}],
                },
            ).status_code
            == 422
        )
        response = outsider.post(
            f"/api/v1/contracts/{existing['id']}/transitions",
            json={"action": "archive", "revision": existing["revision"]},
        )
        assert response.status_code == 404
        assert existing["id"] not in outsider.get("/api/v1/audit").text
    assert client.get("/api/v1/contracts").json()["total"] == 1


def test_invitation_roles_switching_and_api_key_binding(client, app):
    owner = login(client)
    key = client.post("/api/v1/auth/api-keys", json={"name": "Scoped", "days": 1}).json()["token"]
    token = invite(client, "new@example.com")
    with browser(app) as member:
        new = signup(member)
        own_org = new["user"]["organization_id"]
        accepted = member.post("/api/v1/invitations/accept", json={"token": token})
        assert accepted.status_code == 200, accepted.text
        assert member.post("/api/v1/invitations/accept", json={"token": token}).status_code == 400
        switched = member.post(f"/api/v1/organizations/{owner['organization_id']}/activate").json()
        assert switched["role"] == "viewer" and not switched["is_owner"]
        assert member.post("/api/v1/contracts", json=contract_payload(member)).status_code == 403
        assert member.get("/api/v1/organizations/current/members").status_code == 403
        assert member.post(f"/api/v1/organizations/{own_org}/activate").json()["role"] == "admin"
        assert (
            member.get(
                "/api/v1/contracts", headers={"X-Organization-ID": owner["organization_id"]}
            ).status_code
            == 409
        )
    second = client.post("/api/v1/organizations", json={"name": "Second workspace"}).json()
    assert client.post(f"/api/v1/organizations/{second['id']}/activate").status_code == 200
    assert client.get("/api/v1/auth/api-keys").json() == []
    headers = {"Authorization": "Bearer " + key}
    assert (
        client.get("/api/v1/auth/me", headers=headers).json()["organization_id"]
        == owner["organization_id"]
    )
    assert (
        client.post(f"/api/v1/organizations/{second['id']}/activate", headers=headers).status_code
        == 403
    )


def test_role_change_and_removal_take_effect_for_existing_sessions_and_api_keys(client, app):
    owner = login(client)
    token = invite(client, "new@example.com", "editor")
    with browser(app) as member:
        new = signup(member)
        own_org = new["user"]["organization_id"]
        own_key = member.post("/api/v1/auth/api-keys", json={"name": "Own", "days": 1}).json()[
            "token"
        ]
        assert member.post("/api/v1/invitations/accept", json={"token": token}).status_code == 200
        assert (
            member.post(f"/api/v1/organizations/{owner['organization_id']}/activate").status_code
            == 200
        )
        member_key = member.post(
            "/api/v1/auth/api-keys", json={"name": "Shared", "days": 1}
        ).json()["token"]
        payload = contract_payload(member)
        assert member.post("/api/v1/contracts", json=payload).status_code == 201
        assert (
            client.patch(
                f"/api/v1/organizations/current/members/{new['user']['id']}",
                json={"role": "viewer"},
            ).status_code
            == 200
        )
        assert member.post("/api/v1/contracts", json=payload).status_code == 403
        assert (
            member.post(
                "/api/v1/contracts", json=payload, headers={"Authorization": "Bearer " + member_key}
            ).status_code
            == 403
        )
        assert (
            client.delete(f"/api/v1/organizations/current/members/{new['user']['id']}").status_code
            == 204
        )
        assert member.get("/api/v1/contracts").status_code == 401
        assert (
            member.get(
                "/api/v1/contracts", headers={"Authorization": "Bearer " + member_key}
            ).status_code
            == 401
        )
        assert (
            member.get("/api/v1/auth/me", headers={"Authorization": "Bearer " + own_key}).json()[
                "organization_id"
            ]
            == own_org
        )


def test_owner_cannot_be_removed_or_demoted(client):
    owner = login(client)
    path = f"/api/v1/organizations/current/members/{owner['id']}"
    assert client.delete(path).status_code == 409
    assert client.patch(path, json={"role": "viewer"}).status_code == 409
    users = client.get("/api/v1/users").json()
    reviewer = next(u for u in users if u["role"] == "reviewer")
    assert (
        client.post(
            "/api/v1/organizations/current/transfer-ownership", json={"user_id": reviewer["id"]}
        ).status_code
        == 200
    )
    assert client.patch(path, json={"role": "viewer"}).status_code == 200
    assert client.get("/api/v1/auth/me").json()["role"] == "viewer"


@pytest.mark.parametrize("invalid", ["wrong_recipient", "expired", "revoked"])
def test_invalid_invites_cannot_join(client, app, invalid):
    login(client)
    token = invite(client, "new@example.com")
    with app.state.sessions() as db:
        item = db.scalar(select(Invitation))
        if invalid == "expired":
            item.expires_at = now() - timedelta(seconds=1)
        elif invalid == "revoked":
            item.consumed_at = now()
        db.commit()
    with browser(app) as member:
        signup(member, "wrong@example.com" if invalid == "wrong_recipient" else "new@example.com")
        assert member.post("/api/v1/invitations/accept", json={"token": token}).status_code == 400
        assert len(member.get("/api/v1/organizations").json()) == 1


def test_recovery_code_single_use_and_revokes_all_credentials(client, app):
    result = signup(client)
    cookie = client.cookies.get("openclm_session")
    api_key = client.post("/api/v1/auth/api-keys", json={"name": "Before reset", "days": 1}).json()[
        "token"
    ]
    payload = {
        "email": "new@example.com",
        "code": result["recovery_codes"][0],
        "password": "replacement-password-2026",
    }
    response = client.post("/api/v1/auth/recover", json=payload)
    assert response.status_code == 200 and len(response.json()["recovery_codes"]) == 6
    assert client.post("/api/v1/auth/recover", json=payload).status_code == 400
    assert (
        client.get("/api/v1/contracts", headers={"Cookie": "openclm_session=" + cookie}).status_code
        == 401
    )
    assert (
        client.get("/api/v1/contracts", headers={"Authorization": "Bearer " + api_key}).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login", json={"email": "new@example.com", "password": payload["password"]}
        ).status_code
        == 200
    )
    with app.state.sessions() as db:
        stored = db.scalars(select(Token).where(Token.kind == "recovery")).all()
        assert all(t.token_hash not in response.json()["recovery_codes"] for t in stored)


def test_database_rejects_cross_organization_foreign_key(client, app):
    existing = create_contract(client)
    with browser(app) as outsider:
        new = signup(outsider)
    with app.state.sessions() as db:
        row = db.get(Contract, existing["id"])
        row.organization_id = new["user"]["organization_id"]
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_request_session_has_no_implicit_global_scope(app):
    with app.state.sessions() as db:
        db.info["tenant_required"] = True
        assert db.scalars(select(Contract)).all() == []
        from openclm.models import Template

        assert db.scalars(select(Template)).all() == []


def test_limits_and_signup_switch(client, app):
    app.state.settings.max_organizations_per_user = 1
    result = signup(client)
    assert client.post("/api/v1/organizations", json={"name": "Extra"}).status_code == 409
    app.state.settings.max_contracts_per_organization = 1
    payload = contract_payload(client)
    assert client.post("/api/v1/contracts", json=payload).status_code == 201
    assert client.post("/api/v1/contracts", json=payload).status_code == 409
    assert client.get("/api/v1/contracts").json()["total"] == 1
    app.state.settings.public_signup_enabled = False
    assert (
        client.post(
            "/api/v1/auth/register",
            json={
                "name": "Another",
                "email": "other@example.com",
                "password": PASSWORD,
                "organization_name": "Other",
            },
        ).status_code
        == 403
    )
    assert result["user"]["is_owner"]


def test_legacy_seed_never_joins_independent_organizations(app):
    # Use a fresh database: one self-service identity/organization, no legacy space.
    from openclm.db import LEGACY_ORGANIZATION_ID, Base, make_engine, session_factory
    from openclm.models import Membership, Organization, User
    from openclm.seed import seed_templates

    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    with session_factory(engine)() as db:
        user = User(
            email="separate@example.com", name="Separate", role="viewer", password_hash="unused"
        )
        db.add(user)
        db.flush()
        org = Organization(name="Separate space", owner_id=user.id)
        db.add(org)
        db.flush()
        db.add(Membership(organization_id=org.id, user_id=user.id, role="admin"))
        db.commit()
        with pytest.raises(ValueError, match="existing organization"):
            seed_templates(db)
        assert db.get(Organization, LEGACY_ORGANIZATION_ID) is None
        assert len(db.scalars(select(Membership)).all()) == 1
    engine.dispose()


def test_forwarded_rate_limit_ip_requires_proxy_secret(app):
    from starlette.requests import Request

    from openclm.security import client_address

    app.state.settings.proxy_secret = "test-proxy-secret"
    scope = {
        "type": "http",
        "app": app,
        "client": ("127.0.0.1", 1234),
        "headers": [(b"x-openclm-client-ip", b"192.0.2.10")],
    }
    assert client_address(Request(scope)) == "127.0.0.1"
    scope["headers"].append((b"x-openclm-proxy-key", b"test-proxy-secret"))
    assert client_address(Request(scope)) == "192.0.2.10"

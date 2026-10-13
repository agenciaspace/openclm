"""Regression checks for the existing single-organization security boundary."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from openclm.models import Token, User, now

from .conftest import PASSWORD, contract_payload, create_contract, login


@pytest.mark.parametrize(
    "resource",
    [
        "/users",
        "/templates",
        "/workflows",
        "/dashboard",
        "/audit",
        "/auth/api-keys",
        "/settings/capabilities",
        "/contracts",
        "/contracts/{id}",
        "/contracts/{id}/answers",
        "/contracts/{id}/versions",
        "/contracts/{id}/document",
        "/contracts/{id}/document?format=txt",
        "/contracts/{id}/signature",
        "/audit?contract_id={id}",
    ],
)
def test_logout_blocks_every_private_surface(client, resource):
    contract = create_contract(client)
    stale_cookie = client.cookies.get("openclm_session")
    assert client.post("/api/v1/auth/logout").status_code == 204
    path = "/api/v1" + resource.format(id=contract["id"])
    assert client.get(path).status_code == 401
    # A copied cookie must stop working after logout, too.
    assert (
        client.get(path, headers={"Cookie": f"openclm_session={stale_cookie}"}).status_code == 401
    )


@pytest.mark.parametrize("role", ["admin", "editor", "reviewer", "viewer"])
def test_role_matrix_for_contracts_and_user_creation(client, role):
    login(client, role)
    response = client.post("/api/v1/contracts", json=contract_payload(client))
    assert response.status_code == (201 if role in {"admin", "editor"} else 403)
    response = client.post(
        "/api/v1/users",
        json={
            "email": "new-member@example.com",
            "name": "Synthetic member",
            "role": "admin",
            "password": PASSWORD,
        },
    )
    assert response.status_code == (201 if role == "admin" else 403)
    assert client.get("/api/v1/audit").status_code == (200 if role == "admin" else 403)


@pytest.mark.parametrize("credential", ["session", "api"])
@pytest.mark.parametrize("reason", ["disabled", "expired"])
def test_access_ends_when_user_disabled_or_token_expires(client, app, credential, reason):
    user = login(client, "editor")
    headers = {}
    if credential == "api":
        response = client.post("/api/v1/auth/api-keys", json={"name": "boundary-test", "days": 1})
        assert response.status_code == 201
        headers = {"Authorization": f"Bearer {response.json()['token']}"}
    assert client.get("/api/v1/contracts", headers=headers).status_code == 200
    with app.state.sessions() as db:
        if reason == "disabled":
            db.get(User, user["id"]).active = False
        else:
            for token in db.scalars(select(Token).where(Token.user_id == user["id"])):
                token.expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/v1/contracts", headers=headers).status_code == 401

from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from openclm.config import Settings, validate_salesforce_origin
from openclm.models import SalesforceConnection, SalesforceLink, SalesforceState, User
from openclm.oauth import decrypt, encrypt
from tests.conftest import create_contract, login


def enable(app):
    cfg = app.state.settings
    cfg.salesforce_enabled = True
    cfg.salesforce_client_id = "test-client"
    cfg.salesforce_client_secret = "test-secret"
    return cfg


def test_disabled_and_roles(client, app):
    login(client, "admin")
    assert client.get("/api/v1/integrations/salesforce/status").json() == {
        "configured": False,
        "connected": False,
        "instance_url": None,
    }
    assert client.post("/api/v1/integrations/salesforce/connect").status_code == 503
    enable(app)
    login(client, "viewer")
    assert client.post("/api/v1/integrations/salesforce/connect").status_code == 403
    assert client.get("/api/v1/integrations/salesforce/opportunities").status_code == 403


def test_oauth_pkce_encrypted_single_use_and_disconnect(client, app, monkeypatch):
    cfg = enable(app)
    login(client, "admin")
    result = client.post("/api/v1/integrations/salesforce/connect").json()
    params = parse_qs(urlparse(result["authorization_url"]).query)
    assert params["code_challenge_method"] == ["S256"]
    with app.state.sessions() as db:
        state = db.scalar(select(SalesforceState))
        assert decrypt(cfg, state.verifier_encrypted) not in state.verifier_encrypted

    def exchange(cfg, data):
        assert len(data["code_verifier"]) >= 43
        return {
            "access_token": "access-secret",
            "refresh_token": "refresh-secret",
            "instance_url": "https://acme.my.salesforce.com",
        }

    monkeypatch.setattr("openclm.salesforce.exchange", exchange)
    callback = "/api/v1/integrations/salesforce/callback?state=" + params["state"][0] + "&code=test"
    response = client.get(callback, follow_redirects=False)
    assert response.headers["location"] == "/?salesforce=connected"
    assert client.get(callback).status_code == 400
    with app.state.sessions() as db:
        connection = db.scalar(select(SalesforceConnection))
        assert "access-secret" not in connection.access_encrypted
        assert decrypt(cfg, connection.access_encrypted) == "access-secret"
    assert "secret" not in client.get("/api/v1/integrations/salesforce/status").text
    assert client.delete("/api/v1/integrations/salesforce/connection").status_code == 204
    assert client.get("/api/v1/integrations/salesforce/status").json()["connected"] is False


def test_state_bound_to_browser_session(client, app):
    enable(app)
    login(client, "admin")
    state = parse_qs(
        urlparse(
            client.post("/api/v1/integrations/salesforce/connect").json()["authorization_url"]
        ).query
    )["state"][0]
    login(client, "admin")
    assert (
        client.get(
            "/api/v1/integrations/salesforce/callback", params={"state": state, "code": "x"}
        ).status_code
        == 400
    )


@pytest.mark.parametrize(
    "origin",
    [
        "http://acme.my.salesforce.com",
        "https://salesforce.com.evil.test",
        "https://127.0.0.1",
        "https://a.my.salesforce.com@evil.test",
        "https://a.my.salesforce.com/redirect",
        "https://a.my.salesforce.com:8443",
    ],
)
def test_reject_untrusted_origins(origin):
    with pytest.raises(ValueError):
        validate_salesforce_origin(origin)


def test_configuration_requires_keys():
    with pytest.raises(ValueError):
        Settings(_env_file=None, salesforce_enabled=True)


def test_search_escape_and_link_authorization(client, app, monkeypatch):
    cfg = enable(app)
    contract = create_contract(client)
    queries = []
    with app.state.sessions() as db:
        user = db.scalar(select(User).where(User.role == "admin"))
        connection = SalesforceConnection(
            user_id=user.id,
            instance_url="https://acme.my.salesforce.com",
            access_encrypted=encrypt(cfg, "a"),
            refresh_encrypted=encrypt(cfg, "r"),
        )
        db.add(connection)
        db.commit()

    def query(cfg, db, user_id, soql):
        queries.append(soql)
        return db.get(SalesforceConnection, user_id), [
            {"Id": "006000000000001", "Name": "Opportunity", "Account": {"Name": "Acme"}}
        ]

    monkeypatch.setattr("openclm.salesforce.query", query)
    assert (
        client.get(
            "/api/v1/integrations/salesforce/opportunities", params={"q": "O'Reilly%"}
        ).status_code
        == 200
    )
    assert "O\\'Reilly\\%" in queries[-1]
    path = f"/api/v1/integrations/salesforce/contracts/{contract['id']}"
    assert client.post(path, json={"opportunity_id": "006000000000001'"}).status_code == 422
    assert client.post(path, json={"opportunity_id": "006000000000001"}).status_code == 200
    with app.state.sessions() as db:
        assert db.get(SalesforceLink, contract["id"]).opportunity_name == "Opportunity"
    login(client, "editor")
    assert client.post(path, json={"opportunity_id": "006000000000001"}).status_code == 403
    assert (
        client.get(path).json()["link"]["url"] == "https://acme.my.salesforce.com/006000000000001"
    )


def test_expired_access_refreshes_once(client, app, monkeypatch):
    import httpx

    from openclm.salesforce import query

    cfg = enable(app)
    with app.state.sessions() as db:
        user = db.scalar(select(User).where(User.role == "admin"))
        db.add(
            SalesforceConnection(
                user_id=user.id,
                instance_url="https://acme.my.salesforce.com",
                access_encrypted=encrypt(cfg, "old"),
                refresh_encrypted=encrypt(cfg, "refresh"),
            )
        )
        db.commit()
        requests = []

        def handler(request):
            requests.append(request)
            if request.url.path.endswith("/token"):
                return httpx.Response(200, json={"access_token": "new", "refresh_token": "rotated"})
            if request.headers["authorization"] == "Bearer old":
                return httpx.Response(401)
            return httpx.Response(200, json={"records": []})

        original = httpx.Client
        monkeypatch.setattr(
            "openclm.salesforce.httpx.Client",
            lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
        )
        connection, records = query(cfg, db, user.id, "SELECT Id FROM Opportunity LIMIT 1")
        assert records == []
        assert len(requests) == 3
        assert decrypt(cfg, connection.refresh_encrypted) == "rotated"
        assert all(
            r.url.host in {"acme.my.salesforce.com", "test.salesforce.com"} for r in requests
        )

import base64
import hashlib
import hmac
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from openclm.config import Settings
from openclm.integrations import DocuSign
from openclm.models import DocuSignConnection, OAuthState, SignatureRequest, now
from openclm.oauth import decrypt

from .conftest import approved_contract, create_contract, enable_docusign, login

ENVELOPE = "12345678-1234-1234-1234-123456789abc"


def transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda *args, **kwargs: original(*args, **kwargs, transport=httpx.MockTransport(handler)),
    )


def oauth_mock(request):
    if request.url.path == "/oauth/token":
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(
            200,
            json={
                "access_token": "new-private-access",
                "refresh_token": "new-private-refresh",
                "expires_in": 3600,
            },
        )
    if request.url.path == "/oauth/userinfo":
        return httpx.Response(
            200,
            json={
                "sub": "provider-user",
                "accounts": [
                    {
                        "account_id": "account-1",
                        "account_name": "Demo Account",
                        "is_default": True,
                        "base_uri": "https://demo.docusign.net",
                    }
                ],
            },
        )
    raise AssertionError(f"Unexpected outgoing request: {request.url}")


def test_oauth_connect_callback_encryption_replay_and_disconnect(client, app, monkeypatch):
    user = login(client)
    cfg = enable_docusign(app, user["id"])
    # Start with no user connection, just the installation's app credentials.
    client.delete("/api/v1/integrations/docusign/connection")
    transport(monkeypatch, oauth_mock)
    response = client.post("/api/v1/integrations/docusign/connect")
    auth_url = response.json()["authorization_url"]
    params = parse_qs(urlparse(auth_url).query)
    assert params["scope"] == ["signature extended"]
    assert params["redirect_uri"] == ["https://testserver/api/v1/integrations/docusign/callback"]
    assert "client-secret" not in auth_url
    state = params["state"][0]
    with app.state.sessions() as db:
        saved = db.scalar(select(OAuthState))
        assert saved.state_hash != state
    callback = f"/api/v1/integrations/docusign/callback?code=one-time-code&state={state}"
    completed = client.get(callback, follow_redirects=False)
    assert completed.status_code == 303 and completed.headers["location"] == "/?docusign=connected"
    assert client.get(callback).status_code == 400
    status = client.get("/api/v1/integrations/docusign/status").json()
    assert status["connected"] and status["account_name"] == "Demo Account"
    assert "token" not in json.dumps(status)
    with app.state.sessions() as db:
        connection = db.get(DocuSignConnection, user["id"])
        assert "new-private" not in connection.access_encrypted
        assert decrypt(cfg, connection.refresh_encrypted) == "new-private-refresh"
    assert client.delete("/api/v1/integrations/docusign/connection").status_code == 204
    assert not client.get("/api/v1/integrations/docusign/status").json()["connected"]


def test_oauth_state_bound_to_user_session_and_expiry(client, app):
    user = login(client)
    enable_docusign(app, user["id"])
    url = client.post("/api/v1/integrations/docusign/connect").json()["authorization_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    login(client, "editor")
    assert (
        client.get(
            f"/api/v1/integrations/docusign/callback?state={state}&code=anything"
        ).status_code
        == 400
    )
    login(client)
    # Even the same user in a new session cannot redeem it.
    assert (
        client.get(
            f"/api/v1/integrations/docusign/callback?state={state}&code=anything"
        ).status_code
        == 400
    )
    url = client.post("/api/v1/integrations/docusign/connect").json()["authorization_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    with app.state.sessions() as db:
        for row in db.scalars(select(OAuthState)):
            row.expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert (
        client.get(
            f"/api/v1/integrations/docusign/callback?state={state}&code=anything"
        ).status_code
        == 400
    )


def test_oauth_cancel_and_failure_are_safe(client, app, monkeypatch):
    user = login(client)
    enable_docusign(app, user["id"])

    def start():
        url = client.post("/api/v1/integrations/docusign/connect").json()["authorization_url"]
        return parse_qs(urlparse(url).query)["state"][0]

    state = start()
    r = client.get(
        f"/api/v1/integrations/docusign/callback?state={state}&error=access_denied",
        follow_redirects=False,
    )
    assert r.headers["location"] == "/?docusign=cancelled"
    state = start()
    transport(
        monkeypatch, lambda request: httpx.Response(400, json={"error": "secret-provider-error"})
    )
    r = client.get(
        f"/api/v1/integrations/docusign/callback?state={state}&code=bad", follow_redirects=False
    )
    assert r.headers["location"] == "/?docusign=failed"
    assert "secret-provider" not in r.text


def test_automatic_refresh_rotates_encrypted_credentials(client, app, monkeypatch):
    user = login(client)
    cfg = enable_docusign(app, user["id"])
    calls = []

    def handler(request):
        calls.append(request)
        assert b"grant_type=refresh_token" in request.content
        assert b"private-refresh-token" in request.content
        return oauth_mock(request)

    transport(monkeypatch, handler)
    with app.state.sessions() as db:
        connection = db.get(DocuSignConnection, user["id"])
        connection.expires_at = now() - timedelta(seconds=1)
        db.commit()
        with DocuSign(cfg, db, connection).client() as provider:
            assert provider.headers["authorization"] == "Bearer new-private-access"
        assert decrypt(cfg, connection.refresh_encrypted) == "new-private-refresh"
        assert connection.expires_at > now()
    assert len(calls) == 1


def test_signature_send_requires_approval_connection_and_consent(client, app, monkeypatch):
    c = create_contract(client)
    user = client.get("/api/v1/auth/me").json()
    enable_docusign(app, user["id"])
    payload = {
        "revision": c["revision"],
        "signers": [{"name": "Ana", "email": "ana@example.com"}],
        "consent_to_external_transfer": True,
    }
    assert client.post(f"/api/v1/contracts/{c['id']}/signature", json=payload).status_code == 409
    c = approved_contract(client)
    payload["revision"] = c["revision"]
    path = f"/api/v1/contracts/{c['id']}/signature"
    assert (
        client.post(path, json={**payload, "consent_to_external_transfer": False}).status_code
        == 422
    )
    sent = []

    def handler(request):
        assert request.url.path == "/restapi/v2.1/accounts/account-1/envelopes"
        body = json.loads(request.content)
        assert body["eventNotification"]["integratorManaged"] == "true"
        assert body["eventNotification"]["includeHMAC"] == "true"
        assert body["eventNotification"]["deliveryMode"] == "SIM"
        assert base64.b64decode(body["documents"][0]["documentBase64"]).startswith(b"PK")
        assert (
            body["recipients"]["signers"][0]["tabs"]["signHereTabs"][0]["anchorIgnoreIfNotPresent"]
            == "false"
        )
        sent.append(body)
        return httpx.Response(201, json={"envelopeId": ENVELOPE})

    transport(monkeypatch, handler)
    response = client.post(path, json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["envelope_id"] == ENVELOPE
    assert client.post(path, json=payload).status_code == 409
    assert len(sent) == 1


def test_uncertain_send_reserved_no_duplicate(client, app, monkeypatch):
    c = approved_contract(client)
    enable_docusign(app, c["owner_id"])

    def timeout(request):
        raise httpx.ReadTimeout("timeout")

    transport(monkeypatch, timeout)
    path = f"/api/v1/contracts/{c['id']}/signature"
    payload = {
        "revision": c["revision"],
        "signers": [{"name": "Ana", "email": "ana@example.com"}],
        "consent_to_external_transfer": True,
    }
    assert client.post(path, json=payload).status_code == 502
    assert client.get(path).json()["status"] == "dispatch_uncertain"
    assert client.post(path, json=payload).status_code == 409


def test_hmac_webhook_replay_account_binding_and_terminal_status(client, app):
    c = approved_contract(client)
    cfg = enable_docusign(app, c["owner_id"])
    with app.state.sessions() as db:
        db.add(
            SignatureRequest(
                contract_id=c["id"],
                sender_id=c["owner_id"],
                account_id="account-1",
                envelope_id=ENVELOPE,
                document_version=1,
                status="sent",
                signers=[],
            )
        )
        db.commit()
    payload = {
        "event": "envelope-completed",
        "data": {"envelopeId": ENVELOPE, "accountId": "account-1"},
    }

    def notify(body, valid=True):
        raw = json.dumps(body).encode()
        signature = base64.b64encode(
            hmac.new(cfg.docusign_hmac_secret.encode(), raw, hashlib.sha256).digest()
        ).decode()
        return client.post(
            "/api/v1/webhooks/docusign",
            content=raw,
            headers={"X-DocuSign-Signature-1": signature if valid else "invalid"},
        )

    assert notify(payload, False).status_code == 401
    assert (
        notify({**payload, "data": {**payload["data"], "accountId": "other-account"}}).status_code
        == 422
    )
    assert notify(payload).status_code == 200
    assert notify(payload).json()["duplicate"] is True
    assert notify({**payload, "event": "envelope-sent"}).status_code == 200
    assert client.get(f"/api/v1/contracts/{c['id']}").json()["status"] == "signed"


def test_local_ai_disabled_and_grounded_request(client, app, monkeypatch):
    c = create_contract(client)
    path = f"/api/v1/contracts/{c['id']}/ai"
    assert client.post(path, json={"task": "summary"}).status_code == 503
    cfg = app.state.settings
    cfg.ai_enabled = True
    calls = []

    def handler(request):
        assert request.url.host == "127.0.0.1"
        calls.append(request.url.path)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": cfg.ollama_model}]})
        payload = json.loads(request.content)
        assert payload["stream"] is False
        assert c["content"] in payload["messages"][1]["content"]
        assert "tools" not in payload
        return httpx.Response(200, json={"message": {"content": "Resumo para revisão."}})

    transport(monkeypatch, handler)
    r = client.post(path, json={"task": "summary"})
    assert r.status_code == 200 and r.json()["requires_review"]
    assert calls == ["/api/tags", "/api/chat"]


def test_ai_refuses_cloud_tag_even_from_private_server(client, app, monkeypatch):
    c = create_contract(client)
    app.state.settings.ai_enabled = True
    transport(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={
                "models": [
                    {"name": app.state.settings.ollama_model, "remote_host": "https://ollama.com"}
                ]
            },
        ),
    )
    assert (
        client.post(f"/api/v1/contracts/{c['id']}/ai", json={"task": "summary"}).status_code == 503
    )


@pytest.mark.parametrize(
    "settings",
    [
        {"ai_enabled": True, "ollama_url": "https://ollama.com"},
        {"ai_enabled": True, "ollama_model": "large:cloud"},
        {"ai_enabled": True, "ollama_url": "http://169.254.169.254"},
        {"app_url": "https://clm.example.com", "secure_cookies": False},
        {"docusign_enabled": True},
    ],
)
def test_invalid_external_config_rejected(settings):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **settings)


def test_secure_cookie_and_webhook_without_session(app):
    with TestClient(app, base_url="https://testserver", headers={"X-OpenCLM": "1"}) as client:
        login(client)
        assert client.cookies.get("openclm_session")
        # Login cookie must survive the OAuth top-level GET callback, while writes use CSRF guards.
        r = client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.com", "password": "local-test-password-2026"},
        )
        assert (
            "SameSite=lax" in r.headers["set-cookie"]
            and "Secure" in r.headers["set-cookie"]
            and "HttpOnly" in r.headers["set-cookie"]
        )

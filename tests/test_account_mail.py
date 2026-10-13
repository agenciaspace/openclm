import json

from sqlalchemy import select

from openclm.mail import drain_outbox
from openclm.models import MailOutbox, Token
from openclm.oauth import decrypt

from .test_organizations import signup


def test_verification_and_password_email_tokens_are_private_single_use(client, app, monkeypatch):
    app.state.settings.mail_transport = "smtp"
    app.state.settings.smtp_host = "localhost"
    result = signup(client)
    assert result["verification_queued"]
    with app.state.sessions() as db:
        item = db.scalar(select(MailOutbox))
        message = json.loads(decrypt(app.state.settings, item.payload_encrypted))
        token = message["text"].split("#verify=")[1].split()[0]
        assert token not in item.payload_encrypted
        assert db.scalar(select(Token.token_hash).where(Token.kind == "verify")) != token
    assert client.post("/api/v1/auth/verify-email", json={"token": token}).status_code == 200
    assert client.get("/api/v1/auth/me").json()["email_verified"]
    assert client.post("/api/v1/auth/verify-email", json={"token": token}).status_code == 400
    existing = client.post("/api/v1/auth/forgot-password", json={"email": "new@example.com"})
    unknown = client.post("/api/v1/auth/forgot-password", json={"email": "unknown@example.com"})
    assert existing.status_code == unknown.status_code == 202
    assert existing.json() == unknown.json()
    with app.state.sessions() as db:
        messages = [
            json.loads(decrypt(app.state.settings, x.payload_encrypted))
            for x in db.scalars(select(MailOutbox))
        ]
        reset = (
            next(x for x in messages if "#reset=" in x["text"])["text"]
            .split("#reset=")[1]
            .split()[0]
        )
    response = client.post(
        "/api/v1/auth/reset-password", json={"token": reset, "password": "new-mail-password-2026"}
    )
    assert response.status_code == 200
    assert (
        client.post(
            "/api/v1/auth/reset-password",
            json={"token": reset, "password": "new-mail-password-2026"},
        ).status_code
        == 400
    )
    delivered = []
    monkeypatch.setattr("openclm.mail.deliver", lambda cfg, message: delivered.append(message))
    drain_outbox(app)
    drain_outbox(app)
    assert len(delivered) == 2
    with app.state.sessions() as db:
        assert all(
            item.status == "sent" and item.payload_encrypted == ""
            for item in db.scalars(select(MailOutbox))
        )


def test_mail_failure_retries_without_exposing_token_or_recipient(client, app, monkeypatch, caplog):
    app.state.settings.mail_transport = "smtp"
    signup(client)

    def fail(cfg, message):
        raise RuntimeError("private delivery contents " + message["text"])

    monkeypatch.setattr("openclm.mail.deliver", fail)
    drain_outbox(app)
    with app.state.sessions() as db:
        item = db.scalar(select(MailOutbox))
        assert item.status == "pending" and item.attempts == 1
    assert "#verify=" not in caplog.text and "new@example.com" not in caplog.text

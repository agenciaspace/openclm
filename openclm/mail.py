"""Encrypted transactional outbox. Delivery retries never expose action tokens."""

import asyncio
import hashlib
import hmac
import json
import logging
import smtplib
import ssl
import time
from datetime import timedelta
from email.message import EmailMessage
from email.utils import parseaddr

import httpx
from sqlalchemy import delete, select, update

from .models import MailOutbox, now
from .oauth import decrypt, encrypt

log = logging.getLogger(__name__)


def queue_mail(db, cfg, recipient, subject, text):
    if cfg.mail_transport == "disabled":
        return False
    subject = subject.replace("\r", " ").replace("\n", " ")
    db.add(
        MailOutbox(
            payload_encrypted=encrypt(
                cfg, json.dumps({"to": recipient, "subject": subject, "text": text})
            )
        )
    )
    return True


def deliver(cfg, message):
    if cfg.mail_transport == "relay":
        raw = json.dumps(message, ensure_ascii=False).encode()
        timestamp = str(int(time.time()))
        signature = hmac.new(
            cfg.mail_relay_secret.encode(), timestamp.encode() + b"." + raw, hashlib.sha256
        ).hexdigest()
        with httpx.Client(timeout=25, trust_env=False) as client:
            response = client.post(
                cfg.mail_relay_url.rstrip("/") + "/send",
                content=raw,
                headers={
                    "Content-Type": "application/json",
                    "X-Mail-Timestamp": timestamp,
                    "X-Mail-Signature": signature,
                },
            )
            response.raise_for_status()
            if not response.json().get("accepted"):
                raise ValueError("Mail relay declined delivery")
    elif cfg.mail_transport == "smtp":
        email = EmailMessage()
        email["From"], email["To"], email["Subject"] = (
            cfg.mail_from,
            message["to"],
            message["subject"],
        )
        email.set_content(message["text"])
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=20) as smtp:
            if cfg.smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if cfg.smtp_username:
                smtp.login(cfg.smtp_username, cfg.smtp_password)
            smtp.send_message(email)
    elif cfg.mail_transport == "cloudflare":
        sender_name, sender_address = parseaddr(cfg.mail_from)
        with httpx.Client(timeout=20, trust_env=False) as client:
            response = client.post(
                f"https://api.cloudflare.com/client/v4/accounts/{cfg.cloudflare_account_id}/email/sending/send",
                headers={"Authorization": f"Bearer {cfg.cloudflare_email_token}"},
                json={"from": {"address": sender_address, "name": sender_name}, **message},
            )
            response.raise_for_status()
            if not response.json().get("success"):
                raise ValueError("Email provider declined delivery")
    else:
        raise ValueError("Mail delivery is disabled")


def drain_outbox(app):
    cfg = app.state.settings
    if cfg.mail_transport == "disabled":
        return
    with app.state.sessions() as db:
        db.execute(
            update(MailOutbox)
            .where(MailOutbox.status == "sending", MailOutbox.next_attempt_at < now())
            .values(status="pending")
        )
        # Retain only short-lived delivery metadata, never permanent mail bodies.
        db.execute(delete(MailOutbox).where(MailOutbox.created_at < now() - timedelta(days=7)))
        ids = db.scalars(
            select(MailOutbox.id)
            .where(MailOutbox.status == "pending", MailOutbox.next_attempt_at <= now())
            .limit(10)
        ).all()
        db.commit()
        for item_id in ids:
            claim = db.execute(
                update(MailOutbox)
                .where(
                    MailOutbox.id == item_id,
                    MailOutbox.status == "pending",
                    MailOutbox.next_attempt_at <= now(),
                )
                .values(
                    status="sending",
                    next_attempt_at=now() + timedelta(minutes=2),
                    attempts=MailOutbox.attempts + 1,
                )
            )
            db.commit()
            if claim.rowcount != 1:
                continue
            item = db.get(MailOutbox, item_id)
            try:
                deliver(cfg, json.loads(decrypt(cfg, item.payload_encrypted)))
            except Exception:
                # Do not log SMTP/provider exceptions containing recipients or links.
                log.warning("Transactional delivery failed for outbox item %s", item_id)
                item.status = "failed" if item.attempts >= 6 else "pending"
                item.next_attempt_at = now() + timedelta(seconds=min(3600, 30 * 2**item.attempts))
            else:
                item.status = "sent"
                item.payload_encrypted = ""
            db.commit()


async def outbox_loop(app):
    while True:
        try:
            await asyncio.to_thread(drain_outbox, app)
        except Exception:
            log.warning("Transactional outbox is temporarily unavailable")
        await asyncio.sleep(10)

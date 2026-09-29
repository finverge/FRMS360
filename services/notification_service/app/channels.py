"""Delivery channels.

Two real ones and one for development. The development driver writes to a spool directory
and says so; it does not pretend to have sent anything. A notification system that reports
success when no relay exists is worse than no notification system, because the bank stops
checking.

Retries are bounded and backed off. A mail relay that is down for ten minutes should not
lose the message; one that has rejected the same address four times is not going to accept
it on the fifth, and continuing to try turns a configuration error into a queue that never
drains.
"""
from __future__ import annotations

import json
import logging
import pathlib
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import httpx

log = logging.getLogger("notify.channels")

SPOOL = pathlib.Path("var/notifications")

#: Defaults. A bank with a flaky internal relay wants more patience than one whose
#: webhook is a hosted incident queue, so both are overridable per tenant per channel -
#: see ``retry_policy``.
MAX_ATTEMPTS = 5
BACKOFF_MINUTES = (1, 5, 15, 60)
#: Guard rails on what a tenant may configure. Unbounded attempts turn a permanent
#: misconfiguration into a queue that never drains and a log nobody reads.
MAX_ATTEMPTS_CEILING = 20
BACKOFF_CEILING_MINUTES = 1440


def retry_policy(config: dict | None) -> tuple[int, tuple[int, ...]]:
    """(attempt budget, backoff ladder) for a channel, clamped to something sane."""
    config = config or {}
    attempts = int(config.get("max_attempts") or MAX_ATTEMPTS)
    attempts = max(1, min(attempts, MAX_ATTEMPTS_CEILING))

    ladder = config.get("retry_backoff_minutes") or BACKOFF_MINUTES
    if isinstance(ladder, (int, float)):
        ladder = [ladder]
    try:
        steps = tuple(max(1, min(int(x), BACKOFF_CEILING_MINUTES)) for x in ladder)
    except (TypeError, ValueError):
        steps = BACKOFF_MINUTES
    return attempts, (steps or BACKOFF_MINUTES)


@dataclass
class Result:
    ok: bool
    detail: str


def next_attempt(attempts: int, config: dict | None = None) -> datetime | None:
    """When to try again, or None once the attempt budget is spent."""
    budget, ladder = retry_policy(config)
    if attempts >= budget:
        return None
    idx = min(attempts - 1, len(ladder) - 1)
    return datetime.now(timezone.utc) + timedelta(minutes=ladder[max(0, idx)])


def send_email(config: dict, *, to: str, subject: str, body: str,
               link: str = "", attachment: tuple[str, bytes, str] | None = None) -> Result:
    """Send through the tenant's own relay.

    Bank mail relays sit inside the bank's network, so host, port and credentials are
    tenant configuration rather than platform configuration.

    ``attachment``, if given, is ``(filename, content, mime_type)`` - for a scheduled
    report export, the deliverable itself, not just a notice pointing at one.
    """
    host = (config or {}).get("host", "")
    if not host:
        return Result(False, "no SMTP host configured for this tenant")

    port = int(config.get("port", 25))
    sender = config.get("from", "frms-noreply@localhost")
    username, password = config.get("username", ""), config.get("password", "")
    use_tls = bool(config.get("starttls", True))
    timeout = float(config.get("timeout_seconds", 10))

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to
    message.set_content(body + (f"\n\nOpen: {link}\n" if link else "\n"))
    if attachment:
        filename, content, mime = attachment
        maintype, _, subtype = (mime or "application/octet-stream").partition("/")
        message.add_attachment(content, maintype=maintype or "application",
                               subtype=subtype or "octet-stream", filename=filename)

    try:
        with smtplib.SMTP(host, port, timeout=timeout) as smtp:
            if use_tls:
                try:
                    smtp.starttls()
                except smtplib.SMTPNotSupportedError:
                    # Some internal relays are plaintext on a trusted segment. Recorded
                    # rather than failed, so the operator can see what actually happened.
                    log.warning("relay %s does not support STARTTLS", host)
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
        return Result(True, f"sent to {to} via {host}:{port}")
    except Exception as exc:  # noqa: BLE001
        return Result(False, f"{type(exc).__name__}: {exc}"[:500])


def send_webhook(config: dict, *, payload: dict) -> Result:
    """POST to the tenant's own system - an incident queue, a chat channel, a SIEM."""
    url = (config or {}).get("url", "")
    if not url:
        return Result(False, "no webhook URL configured for this tenant")
    headers = {"Content-Type": "application/json"}
    headers.update(config.get("headers") or {})
    try:
        r = httpx.post(url, json=payload, headers=headers,
                       timeout=float(config.get("timeout_seconds", 10)))
        if r.status_code >= 400:
            return Result(False, f"HTTP {r.status_code}: {r.text[:200]}")
        return Result(True, f"HTTP {r.status_code}")
    except Exception as exc:  # noqa: BLE001
        return Result(False, f"{type(exc).__name__}: {exc}"[:500])


def spool(payload: dict) -> Result:
    """Development driver. Writes the message to disk and says exactly that."""
    SPOOL.mkdir(parents=True, exist_ok=True)
    name = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{payload.get('id', 'x')[:8]}.json"
    path = SPOOL / name
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return Result(True, f"NOT SENT - no channel configured; written to {path}")


def deliver(channel: str, config: dict, *, notification: dict) -> Result:
    if channel == "email":
        return send_email(config, to=notification["recipient"],
                          subject=notification["subject"], body=notification["body"],
                          link=notification.get("link", ""))
    if channel == "webhook":
        return send_webhook(config, payload=notification)
    if channel == "spool":
        return spool(notification)
    return Result(False, f"unknown channel '{channel}'")

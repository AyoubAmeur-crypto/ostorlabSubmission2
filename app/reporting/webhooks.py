"""Webhook signing and delivery of outbox rows.

Signature header (same scheme as most payment providers, customers asked for it):

    X-Tidewright-Signature: t=1792141200,v1=<hex hmac_sha256(secret, f"{t}." + body)>

Receivers recompute the HMAC over the *raw* body, compare in constant time and
reject old timestamps (replay protection); ``verify()`` is the reference.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models import OutboxEntry, OutboxStatus
from app.schemas.webhook import WebhookEnvelope

log = get_logger(__name__)

SIGNATURE_HEADER = "X-Tidewright-Signature"
# 30s, 60s, 120s ... capped at 1h. Not TW_RETRY_*: customer endpoints recover slower.
BACKOFF_BASE_S = 30
BACKOFF_CAP_S = 3600
# Outside prod we only ever talk to the local receiver / fake domains.
DEV_ALLOWED_HOSTS = ("mockhook", "localhost", "127.0.0.1")
DEV_ALLOWED_SUFFIX = ".example.test"


def sign(secret: str, timestamp: int, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"t={timestamp},v1={mac.hexdigest()}"


def verify(secret: str, header: str, body: bytes, *, now: int, tolerance_s: int = 300) -> bool:
    """True if ``header`` is a valid signature of ``body`` made within ``tolerance_s``.

    Several ``v1=`` entries are accepted (one per secret during a rotation).
    """
    timestamp = None
    candidates = []
    for part in (header or "").split(","):
        key, _, value = part.strip().partition("=")
        if key == "t" and value.isdigit():
            timestamp = int(value)
        elif key == "v1" and value:
            candidates.append(value)
    if timestamp is None or not candidates:
        return False
    if abs(now - timestamp) > tolerance_s:
        return False
    expected = sign(secret, timestamp, body).split("v1=", 1)[1]
    # list, not generator: no early exit, every candidate is compared
    return any([hmac.compare_digest(expected, c) for c in candidates])


def backoff_s(attempts: int) -> int:
    """Delay before the next try after ``attempts`` failed attempts (1-based)."""
    return min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** max(0, attempts - 1))


def _host_allowed(url: str) -> bool:
    if get_settings().env == "prod":
        return True
    host = (urlsplit(url).hostname or "").lower()
    return host in DEV_ALLOWED_HOSTS or host.endswith(DEV_ALLOWED_SUFFIX)


def build_body(row: OutboxEntry) -> bytes:
    envelope = WebhookEnvelope(
        id=row.id,
        event=row.event,
        tenant_id=row.tenant_id,
        occurred_at=clock.isoformat(row.created_at),
        data=row.payload or {},
    )
    return json.dumps(envelope.model_dump(mode="json"), separators=(",", ":")).encode()


def dispatch_pending(session: Session, *, limit: int = 50,
                     client: httpx.Client | None = None) -> int:
    """POST due outbox rows to their webhook. Returns the number delivered.

    Rows are locked with SKIP LOCKED so two workers never send the same row
    at the same time. Flushes, never commits (the worker loop owns the scope).
    """
    settings = get_settings()
    now = clock.now()
    rows = session.scalars(
        select(OutboxEntry)
        .where(OutboxEntry.status.in_([OutboxStatus.PENDING.value, OutboxStatus.FAILED.value]))
        .where(OutboxEntry.next_attempt_at <= now)
        .order_by(OutboxEntry.next_attempt_at, OutboxEntry.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    if not rows:
        return 0

    own_client = client is None
    if own_client:
        client = httpx.Client()
    delivered = 0
    try:
        for row in rows:
            hook = row.webhook
            fields = {"outbox_id": row.id, "tenant_id": row.tenant_id, "webhook_id": row.webhook_id,
                      "event": row.event}
            drop = ("webhook disabled" if not hook.enabled
                    else None if _host_allowed(hook.url)
                    else "blocked: only local receivers are allowed outside prod")
            if drop:
                row.status, row.last_error = OutboxStatus.DEAD.value, drop
                log.warning("webhook.dropped", extra={**fields, "error": drop})
                continue

            body = build_body(row)
            headers = {
                "Content-Type": "application/json",
                "X-Tidewright-Event": row.event,
                "X-Tidewright-Delivery": str(row.id),
                SIGNATURE_HEADER: sign(hook.secret, int(now.timestamp()), body),
            }
            row.attempts += 1
            error = None
            try:
                resp = client.post(hook.url, content=body, headers=headers,
                                   timeout=settings.webhook_timeout_s)
                if not 200 <= resp.status_code < 300:
                    error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            except httpx.TimeoutException:
                error = f"timeout after {settings.webhook_timeout_s}s"
            except httpx.HTTPError as exc:
                error = f"{type(exc).__name__}: {exc}"[:500]

            row.last_error = error
            fields.update(attempts=row.attempts, error=error)
            if error is None:
                row.status = OutboxStatus.DELIVERED.value
                row.delivered_at = now
                delivered += 1
                log.info("webhook.delivered", extra=fields)
            elif row.attempts >= settings.webhook_max_attempts:
                row.status = OutboxStatus.DEAD.value
                log.warning("webhook.dead", extra=fields)
            else:
                row.status = OutboxStatus.FAILED.value
                row.next_attempt_at = now + timedelta(seconds=backoff_s(row.attempts))
                log.info("webhook.retry_scheduled", extra=fields)
    finally:
        if own_client:
            client.close()
    session.flush()
    return delivered

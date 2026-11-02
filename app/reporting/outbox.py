"""Transactional outbox: events are written in the transaction of the change that
caused them, delivered later by webhooks.dispatch_pending() (docs/adr/0003)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import ValidationFailed
from app.models import OutboxEntry, OutboxStatus, Webhook, WebhookEvent


def enqueue_event(
    session: Session, tenant_id: int, event: WebhookEvent | str, payload: dict[str, Any]
) -> int:
    """Write one outbox row per enabled webhook of the tenant subscribed to ``event``.

    ``payload`` becomes the envelope's ``data``. Returns the number of rows written
    (0 when nobody listens, which is the common case).
    """
    try:
        event = WebhookEvent(event)
    except ValueError:
        raise ValidationFailed(f"unknown webhook event {event!r}") from None

    hooks = session.scalars(
        select(Webhook)
        .where(Webhook.tenant_id == tenant_id, Webhook.enabled.is_(True))
        .where(Webhook.events.contains([event.value]))
        .order_by(Webhook.id)
    ).all()
    now = clock.now()
    for hook in hooks:
        session.add(
            OutboxEntry(
                tenant_id=tenant_id,
                webhook_id=hook.id,
                event=event.value,
                payload=dict(payload),
                status=OutboxStatus.PENDING.value,
                next_attempt_at=now,
                created_at=now,
            )
        )
    session.flush()
    return len(hooks)

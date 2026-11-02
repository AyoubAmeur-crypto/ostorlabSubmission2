"""Webhook endpoints and the transactional outbox feeding them."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamp, enum_check
from app.models.enums import OutboxStatus, WebhookEvent

if TYPE_CHECKING:
    from app.models.tenant import Tenant


class Webhook(Base):
    __tablename__ = "webhooks"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(2048))
    # HMAC-SHA256 key for X-Tidewright-Signature. Never returned by the API or logged.
    secret: Mapped[str] = mapped_column(String(128))
    events: Mapped[list[str]] = mapped_column(ARRAY(String(32)))
    enabled: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    created_at: Mapped[datetime] = Timestamp(default_now=True)

    tenant: Mapped[Tenant] = relationship(back_populates="webhooks")

    def wants(self, event: str) -> bool:
        return self.enabled and event in (self.events or [])


class OutboxEntry(Base):
    """One pending delivery of one event to one webhook."""

    __tablename__ = "outbox"
    __table_args__ = (
        enum_check("event", WebhookEvent),
        enum_check("status", OutboxStatus),
        Index("ix_outbox_status_next_attempt_at", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    webhook_id: Mapped[int] = mapped_column(ForeignKey("webhooks.id", ondelete="CASCADE"))
    event: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(
        String(16), default=OutboxStatus.PENDING.value, server_default=OutboxStatus.PENDING.value
    )
    attempts: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = Timestamp(default_now=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = Timestamp(default_now=True)
    delivered_at: Mapped[datetime | None] = Timestamp(nullable=True)

    webhook: Mapped[Webhook] = relationship()

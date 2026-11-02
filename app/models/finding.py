"""Findings (normalized, deduplicated results) and their audit trail."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamp, enum_check
from app.models.enums import FindingEventKind, FindingStatus, Severity, Triage

if TYPE_CHECKING:
    from app.models.asset import Asset


class Finding(Base):
    __tablename__ = "findings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "fingerprint"),
        enum_check("severity", Severity),
        enum_check("status", FindingStatus),
        enum_check("triage", Triage),
        Index("ix_findings_tenant_id_status", "tenant_id", "status"),
        Index("ix_findings_tenant_id_last_seen_at", "tenant_id", "last_seen_at"),
        Index("ix_findings_asset_id_plugin", "asset_id", "plugin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    plugin: Mapped[str] = mapped_column(String(64))
    rule_id: Mapped[str] = mapped_column(String(128))
    # sha256 hex, computed by app.ingest.fingerprint (unique per tenant).
    fingerprint: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300))
    severity: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(
        String(16), default=FindingStatus.NEW.value, server_default=FindingStatus.NEW.value
    )
    triage: Mapped[str] = mapped_column(
        String(16), default=Triage.NONE.value, server_default=Triage.NONE.value
    )
    first_seen_at: Mapped[datetime] = Timestamp(default_now=True)
    last_seen_at: Mapped[datetime] = Timestamp(default_now=True)
    fixed_at: Mapped[datetime | None] = Timestamp(nullable=True)
    location: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    scan_id_last: Mapped[int | None] = mapped_column(ForeignKey("scans.id", ondelete="SET NULL"))

    asset: Mapped[Asset] = relationship()
    events: Mapped[list[FindingEvent]] = relationship(
        back_populates="finding",
        order_by="FindingEvent.at",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Finding id={self.id} {self.plugin}/{self.rule_id} {self.status}>"


class FindingEvent(Base):
    """Append-only audit trail. ``actor`` is ``system:ingest``, ``token:<prefix>``,
    ``admin`` or ``cli:<user>``."""

    __tablename__ = "finding_events"
    __table_args__ = (
        enum_check("kind", FindingEventKind),
        Index("ix_finding_events_finding_id_at", "finding_id", "at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    finding_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("findings.id", ondelete="CASCADE")
    )
    at: Mapped[datetime] = Timestamp(default_now=True)
    actor: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )

    finding: Mapped[Finding] = relationship(back_populates="events")

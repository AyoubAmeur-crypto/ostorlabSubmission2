"""Suppression rules (REQ-0150): tenant-defined patterns of findings that are
accepted risk by policy, so customers stop triaging the same finding by hand."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core import clock
from app.models.base import Base, Timestamp


class SuppressionRule(Base):
    __tablename__ = "suppression_rules"
    __table_args__ = (
        Index("ix_suppression_rules_tenant_id_matcher_key", "tenant_id", "matcher_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    plugin: Mapped[str] = mapped_column(String(64))
    rule_id: Mapped[str | None] = mapped_column(String(128))
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    location_prefix: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = Timestamp(nullable=True)
    # "<plugin>\x1f<rule_id or *>", see app.ingest.suppression.matcher_key. Lets
    # ingest fetch the candidate rules for a batch with one indexed IN query.
    matcher_key: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = Timestamp(default_now=True)

    @property
    def active(self) -> bool:
        return self.expires_at is None or self.expires_at > clock.now()

    def __repr__(self) -> str:
        return f"<SuppressionRule id={self.id} {self.plugin}/{self.rule_id or '*'}>"

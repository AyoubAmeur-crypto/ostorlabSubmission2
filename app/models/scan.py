"""Scan profiles (what to run) and scans (one run of a profile on one asset)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamp, enum_check
from app.models.enums import ScanStatus, ScanTrigger

if TYPE_CHECKING:
    from app.models.asset import Asset
    from app.models.tenant import Tenant


class ScanProfile(Base):
    """Which plugins to run with which options, optionally on a cron schedule.

    ``options`` layout (all keys optional)::

        {
          "timeout_s": 20,                 # per-plugin timeout, default TW_PLUGIN_TIMEOUT_S
          "asset_tags": ["prod"],          # scheduler: only assets carrying one of these tags
          "plugins": {"open_ports": {...}} # passed verbatim to that plugin's run()
        }
    """

    __tablename__ = "scan_profiles"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    plugins: Mapped[list[str]] = mapped_column(ARRAY(String(64)))
    options: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    # Five-field cron (m h dom mon dow) in the tenant's time zone; NULL = manual only.
    schedule_cron: Mapped[str | None] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    created_at: Mapped[datetime] = Timestamp(default_now=True)

    tenant: Mapped[Tenant] = relationship(back_populates="profiles")

    def plugin_options(self, plugin: str) -> dict[str, Any]:
        return dict((self.options or {}).get("plugins", {}).get(plugin, {}))

    def __repr__(self) -> str:
        return f"<ScanProfile id={self.id} name={self.name!r}>"


class Scan(Base):
    """State machine: queued -> running -> completed | failed; queued|running -> cancelled."""

    __tablename__ = "scans"
    __table_args__ = (
        enum_check("status", ScanStatus),
        enum_check("trigger", ScanTrigger),
        Index("ix_scans_tenant_id_requested_at", "tenant_id", "requested_at"),
        # Scheduler idempotency: at most one scan per profile, asset and slot.
        Index(
            "uq_scans_profile_id_asset_id_scheduled_slot",
            "profile_id",
            "asset_id",
            "scheduled_slot",
            unique=True,
            postgresql_where=text("scheduled_slot IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    profile_id: Mapped[int] = mapped_column(ForeignKey("scan_profiles.id"), index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    status: Mapped[str] = mapped_column(
        String(16), default=ScanStatus.QUEUED.value, server_default=ScanStatus.QUEUED.value
    )
    trigger: Mapped[str] = mapped_column(String(16), default=ScanTrigger.MANUAL.value)
    requested_at: Mapped[datetime] = Timestamp(default_now=True)
    started_at: Mapped[datetime | None] = Timestamp(nullable=True)
    finished_at: Mapped[datetime | None] = Timestamp(nullable=True)
    # Human-readable failure (or partial-failure) summary; never contains secrets.
    error: Mapped[str | None] = mapped_column(Text)
    # UTC instant of the cron slot that produced this scan (trigger=schedule only).
    scheduled_slot: Mapped[datetime | None] = Timestamp(nullable=True)

    profile: Mapped[ScanProfile] = relationship()
    asset: Mapped[Asset] = relationship()

    def __repr__(self) -> str:
        return f"<Scan id={self.id} status={self.status}>"

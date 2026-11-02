"""Report requests and the files they produce."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamp, enum_check
from app.models.enums import ReportKind, ReportStatus


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        enum_check("kind", ReportKind),
        enum_check("status", ReportStatus),
        Index("ix_reports_tenant_id_created_at", "tenant_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(8))
    # Serialized app.schemas.finding.FindingFilters.
    filters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(16), default=ReportStatus.PENDING.value, server_default=ReportStatus.PENDING.value
    )
    # Absolute path of the generated file under TW_REPORTS_DIR (set when ready).
    path: Mapped[str | None] = mapped_column(String(500))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = Timestamp(default_now=True)
    finished_at: Mapped[datetime | None] = Timestamp(nullable=True)

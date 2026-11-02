"""Report requests."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from app.models.enums import ReportKind, ReportStatus
from app.schemas.common import ReadSchema, Schema
from app.schemas.finding import FindingFilters


class ReportCreate(Schema):
    kind: ReportKind
    filters: FindingFilters = Field(default_factory=FindingFilters)


class ReportRead(ReadSchema):
    """``path`` is internal and never exposed; clients use the download endpoint."""

    id: int
    tenant_id: int
    kind: ReportKind
    filters: dict[str, Any]
    status: ReportStatus
    error: str | None
    created_at: datetime
    finished_at: datetime | None

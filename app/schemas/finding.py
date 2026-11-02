"""Findings, triage and the filter set shared by the list endpoint and reports."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from app.models.enums import FindingEventKind, FindingStatus, Severity, Triage
from app.schemas.common import AwareSchema, ReadSchema, Schema


class FindingFilters(AwareSchema):
    """Filters for ``GET /v1/findings`` and the ``filters`` of a report request.

    Every field is optional; list fields match any of the given values.
    ``since`` keeps findings whose ``last_seen_at >= since``.
    Stored on ``reports.filters`` as ``model_dump(mode="json", exclude_none=True)``.
    """

    severity: list[Severity] | None = None
    status: list[FindingStatus] | None = None
    triage: list[Triage] | None = None
    asset_id: int | None = Field(default=None, gt=0)
    plugin: str | None = Field(default=None, min_length=1, max_length=64)
    since: datetime | None = None

    def to_column(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class FindingRead(ReadSchema):
    id: int
    tenant_id: int
    asset_id: int
    plugin: str
    rule_id: str
    fingerprint: str
    title: str
    severity: Severity
    status: FindingStatus
    triage: Triage
    first_seen_at: datetime
    last_seen_at: datetime
    fixed_at: datetime | None
    location: str
    evidence: dict[str, Any]
    scan_id_last: int | None


class FindingTriageUpdate(Schema):
    """Body of ``PATCH /v1/findings/{id}``. Writes a ``triage_changed`` event."""

    triage: Triage
    note: str | None = Field(default=None, max_length=1000)


class FindingEventRead(ReadSchema):
    id: int
    finding_id: int
    at: datetime
    actor: str
    kind: FindingEventKind
    data: dict[str, Any]

"""Scans."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.models.enums import ScanStatus, ScanTrigger
from app.schemas.common import ReadSchema, Schema


class ScanCreate(Schema):
    profile_id: int = Field(gt=0)
    asset_id: int = Field(gt=0)


class ScanListParams(Schema):
    status: list[ScanStatus] | None = None
    asset_id: int | None = Field(default=None, gt=0)
    profile_id: int | None = Field(default=None, gt=0)


class ScanRead(ReadSchema):
    id: int
    tenant_id: int
    profile_id: int
    asset_id: int
    status: ScanStatus
    trigger: ScanTrigger
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    scheduled_slot: datetime | None

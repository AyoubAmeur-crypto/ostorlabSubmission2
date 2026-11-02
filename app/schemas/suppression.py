"""Suppression rules (REQ-0150). Field names are the contract agreed with
Northpeak's integration (docs/api.md, "Suppression rules")."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from app.schemas.common import AwareSchema, ReadSchema


class SuppressionRuleCreate(AwareSchema):
    """Plugin, rule_id and reason are required; other fields narrow the rule."""

    plugin: str = Field(min_length=1, max_length=64)
    rule_id: str = Field(min_length=1, max_length=128)
    asset_id: int | None = Field(default=None, gt=0)
    location_prefix: str | None = Field(default=None, max_length=1000)
    reason: str = Field(min_length=1, max_length=2000)
    expires_at: datetime | None = None

    @field_validator("location_prefix")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return value or None


class SuppressionRuleRead(ReadSchema):
    id: int
    tenant_id: int
    plugin: str
    rule_id: str | None
    asset_id: int | None
    location_prefix: str | None
    reason: str
    expires_at: datetime | None
    active: bool
    created_by: str
    created_at: datetime

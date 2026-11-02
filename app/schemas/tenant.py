"""Tenants and API tokens (admin endpoints and CLI)."""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator

from app.schemas.common import ReadSchema, Schema

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _check_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"unknown time zone {value!r}") from None
    return value


def _check_ports(value: list[int]) -> list[int]:
    for port in value:
        if not 1 <= port <= 65535:
            raise ValueError(f"port out of range: {port}")
    return sorted(set(value))


class TenantCreate(Schema):
    slug: str = Field(min_length=1, max_length=63)
    name: str = Field(min_length=1, max_length=200)
    timezone: str = "UTC"
    allowed_ports: list[int] = Field(default_factory=lambda: [80, 443])

    @field_validator("slug")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not SLUG_RE.match(value):
            raise ValueError("slug must be lower-case letters, digits and dashes")
        return value

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str) -> str:
        return _check_timezone(value)

    @field_validator("allowed_ports")
    @classmethod
    def _ports(cls, value: list[int]) -> list[int]:
        return _check_ports(value)


class TenantUpdate(Schema):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    timezone: str | None = None
    allowed_ports: list[int] | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str | None) -> str | None:
        return None if value is None else _check_timezone(value)

    @field_validator("allowed_ports")
    @classmethod
    def _ports(cls, value: list[int] | None) -> list[int] | None:
        return None if value is None else _check_ports(value)


class TenantRead(ReadSchema):
    id: int
    slug: str
    name: str
    timezone: str
    allowed_ports: list[int]
    created_at: datetime


class ApiTokenCreate(Schema):
    label: str = Field(default="", max_length=100)


class ApiTokenRead(ReadSchema):
    id: int
    tenant_id: int
    prefix: str
    label: str
    created_at: datetime
    revoked_at: datetime | None


class ApiTokenIssued(ApiTokenRead):
    """Returned once, at creation. ``token`` is the only copy of the plaintext."""

    token: str

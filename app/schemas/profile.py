"""Scan profiles. Plugin names are checked against app.plugins.registry and the
cron expression against app.scheduler.cron by the API; the schema only checks
shape so it stays importable without those packages."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from app.schemas.common import ReadSchema, Schema

MAX_PLUGINS = 16


def _check_plugins(plugins: list[str]) -> list[str]:
    seen: list[str] = []
    for name in plugins:
        name = name.strip()
        if not name:
            raise ValueError("plugin names must not be empty")
        if name in seen:
            raise ValueError(f"plugin {name!r} listed twice")
        seen.append(name)
    return seen


def _check_cron_shape(expr: str | None) -> str | None:
    if expr is None:
        return None
    expr = " ".join(expr.split())
    if not expr:
        return None
    if len(expr.split(" ")) != 5:
        raise ValueError(
            "schedule_cron must have 5 fields: minute hour day-of-month month day-of-week"
        )
    return expr


class ProfileOptions(Schema):
    """Typed view of ``scan_profiles.options`` (see app.models.scan.ScanProfile)."""

    timeout_s: float | None = Field(default=None, gt=0, le=300)
    asset_tags: list[str] | None = None
    plugins: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def to_column(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class ScanProfileCreate(Schema):
    name: str = Field(min_length=1, max_length=100)
    plugins: list[str] = Field(min_length=1, max_length=MAX_PLUGINS)
    options: ProfileOptions = Field(default_factory=ProfileOptions)
    schedule_cron: str | None = None
    enabled: bool = True

    @field_validator("plugins")
    @classmethod
    def _plugins(cls, value: list[str]) -> list[str]:
        return _check_plugins(value)

    @field_validator("schedule_cron")
    @classmethod
    def _cron(cls, value: str | None) -> str | None:
        return _check_cron_shape(value)


class ScanProfileUpdate(Schema):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    plugins: list[str] | None = Field(default=None, min_length=1, max_length=MAX_PLUGINS)
    options: ProfileOptions | None = None
    schedule_cron: str | None = None
    enabled: bool | None = None

    @field_validator("plugins")
    @classmethod
    def _plugins(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _check_plugins(value)

    @field_validator("schedule_cron")
    @classmethod
    def _cron(cls, value: str | None) -> str | None:
        return _check_cron_shape(value)


class ScanProfileRead(ReadSchema):
    id: int
    tenant_id: int
    name: str
    plugins: list[str]
    options: dict[str, Any]
    schedule_cron: str | None
    enabled: bool

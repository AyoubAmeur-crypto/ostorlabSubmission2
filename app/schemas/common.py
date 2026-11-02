"""Base classes and generic shapes used by all schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.clock import ensure_utc

T = TypeVar("T")


class Schema(BaseModel):
    """Input schemas: unknown fields are rejected, strings are stripped."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReadSchema(BaseModel):
    """Output schemas, built from ORM rows with ``Model.model_validate(row)``."""

    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    """List responses. Pagination is ``limit`` + ``offset`` (since v1.5)."""

    items: list[T]
    total: int
    limit: int
    offset: int


class PageParams(Schema):
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorBody(BaseModel):
    """Body of every non-2xx response (see app.core.errors.TidewrightError.to_dict)."""

    error: ErrorDetail


def require_aware(value: datetime | None) -> datetime | None:
    """Validator helper: accept only offset-aware datetimes and normalize to UTC."""
    if value is None:
        return None
    try:
        return ensure_utc(value)
    except ValueError:
        raise ValueError("timestamp must include a UTC offset") from None


class AwareSchema(Schema):
    """Input schema whose datetime fields must carry an offset; values are
    normalized to UTC. Naive timestamps are rejected with a 422."""

    @field_validator("*", mode="after")
    @classmethod
    def _aware(cls, value: Any) -> Any:
        if isinstance(value, datetime):
            return require_aware(value)
        return value

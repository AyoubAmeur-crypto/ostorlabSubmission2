"""Assets. Hostname normalization and duplicate detection happen in the API
router, not here: the schema only bounds the raw input."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import Field, field_validator

from app.models.enums import AssetKind
from app.schemas.common import ReadSchema, Schema

TAG_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,39}$")
MAX_TAGS = 20


def _check_tags(tags: list[str]) -> list[str]:
    if len(tags) > MAX_TAGS:
        raise ValueError(f"at most {MAX_TAGS} tags")
    cleaned: list[str] = []
    for tag in tags:
        tag = tag.strip().lower()
        if not TAG_RE.match(tag):
            raise ValueError(f"invalid tag {tag!r}")
        if tag not in cleaned:
            cleaned.append(tag)
    return cleaned


class AssetCreate(Schema):
    hostname: str = Field(min_length=1, max_length=253)
    kind: AssetKind = AssetKind.WEB
    tags: list[str] = Field(default_factory=list)

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: list[str]) -> list[str]:
        return _check_tags(value)


class AssetUpdate(Schema):
    kind: AssetKind | None = None
    tags: list[str] | None = None

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _check_tags(value)


class AssetRead(ReadSchema):
    id: int
    tenant_id: int
    hostname: str
    kind: AssetKind
    tags: list[str]
    created_at: datetime
    archived_at: datetime | None

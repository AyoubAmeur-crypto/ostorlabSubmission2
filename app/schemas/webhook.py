"""Webhook configuration and the event payloads delivered to customers.

Payload ``data`` objects are built here so ingest, worker and reporting all
emit the same shapes::

    enqueue_event(session, tenant_id, WebhookEvent.FINDING_NEW,
                  FindingEventData.from_finding(finding).model_dump(mode="json"))
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, field_validator

from app.core.clock import isoformat
from app.models.enums import WebhookEvent
from app.schemas.common import ReadSchema, Schema

if TYPE_CHECKING:
    from app.models.finding import Finding
    from app.models.scan import Scan


def _check_url(value: str) -> str:
    if not value.startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://")
    return value


def _check_events(value: list[WebhookEvent]) -> list[WebhookEvent]:
    return sorted(set(value))


class WebhookCreate(Schema):
    url: str = Field(min_length=8, max_length=2048)
    secret: str = Field(min_length=16, max_length=128)
    events: list[WebhookEvent] = Field(min_length=1)
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        return _check_url(value)

    @field_validator("events")
    @classmethod
    def _events(cls, value: list[WebhookEvent]) -> list[WebhookEvent]:
        return _check_events(value)


class WebhookUpdate(Schema):
    url: str | None = Field(default=None, min_length=8, max_length=2048)
    secret: str | None = Field(default=None, min_length=16, max_length=128)
    events: list[WebhookEvent] | None = Field(default=None, min_length=1)
    enabled: bool | None = None

    @field_validator("url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return None if value is None else _check_url(value)

    @field_validator("events")
    @classmethod
    def _events(cls, value: list[WebhookEvent] | None) -> list[WebhookEvent] | None:
        return None if value is None else _check_events(value)


class WebhookRead(ReadSchema):
    """``secret`` is never returned."""

    id: int
    tenant_id: int
    url: str
    events: list[WebhookEvent]
    enabled: bool
    created_at: datetime


class FindingEventData(BaseModel):
    """``data`` of finding.new / finding.reopened / finding.fixed."""

    finding_id: int
    asset_id: int
    plugin: str
    rule_id: str
    title: str
    severity: str
    status: str
    location: str
    first_seen_at: str
    last_seen_at: str
    fixed_at: str | None = None

    @classmethod
    def from_finding(cls, finding: Finding) -> FindingEventData:
        return cls(
            finding_id=finding.id,
            asset_id=finding.asset_id,
            plugin=finding.plugin,
            rule_id=finding.rule_id,
            title=finding.title,
            severity=finding.severity,
            status=finding.status,
            location=finding.location,
            first_seen_at=isoformat(finding.first_seen_at),
            last_seen_at=isoformat(finding.last_seen_at),
            fixed_at=isoformat(finding.fixed_at) if finding.fixed_at else None,
        )


class ScanEventData(BaseModel):
    """``data`` of scan.completed / scan.failed. ``counts`` is IngestSummary.counts()."""

    scan_id: int
    profile_id: int
    asset_id: int
    status: str
    trigger: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)

    @classmethod
    def from_scan(cls, scan: Scan, counts: dict[str, int] | None = None) -> ScanEventData:
        return cls(
            scan_id=scan.id,
            profile_id=scan.profile_id,
            asset_id=scan.asset_id,
            status=scan.status,
            trigger=scan.trigger,
            started_at=isoformat(scan.started_at) if scan.started_at else None,
            finished_at=isoformat(scan.finished_at) if scan.finished_at else None,
            error=scan.error,
            counts=dict(counts or {}),
        )


class WebhookEnvelope(BaseModel):
    """The JSON body POSTed to the customer's URL."""

    id: int  # outbox row id; receivers use it to drop duplicates on retry
    event: WebhookEvent
    tenant_id: int
    occurred_at: str
    data: dict[str, Any]

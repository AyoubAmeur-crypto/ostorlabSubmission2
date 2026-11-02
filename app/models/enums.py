"""Closed value sets stored as text columns.

Columns store the plain string value (``"queued"``), guarded by a CHECK
constraint generated from these enums. Because ``StrEnum`` members are
strings, ``scan.status == ScanStatus.QUEUED`` and ``scan.status = "queued"``
both work.
"""

from __future__ import annotations

from enum import StrEnum


class AssetKind(StrEnum):
    WEB = "web"
    API = "api"
    HOST = "host"


class ScanStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_SCAN_STATUSES


TERMINAL_SCAN_STATUSES = frozenset({ScanStatus.COMPLETED, ScanStatus.FAILED, ScanStatus.CANCELLED})


class ScanTrigger(StrEnum):
    MANUAL = "manual"
    SCHEDULE = "schedule"
    API = "api"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    # Last attempt failed; claimable again once run_after has passed.
    FAILED = "failed"
    # Attempts exhausted; only an operator replay brings it back.
    DEAD = "dead"


class JobKind(StrEnum):
    SCAN_RUN = "scan.run"  # payload {"scan_id": int}
    REPORT_GENERATE = "report.generate"  # payload {"report_id": int}


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


class FindingStatus(StrEnum):
    NEW = "new"
    OPEN = "open"
    FIXED = "fixed"
    REOPENED = "reopened"


class Triage(StrEnum):
    NONE = "none"
    ACCEPTED_RISK = "accepted_risk"
    FALSE_POSITIVE = "false_positive"


class FindingEventKind(StrEnum):
    CREATED = "created"
    REOPENED = "reopened"
    FIXED = "fixed"
    SEVERITY_CHANGED = "severity_changed"
    TRIAGE_CHANGED = "triage_changed"


class WebhookEvent(StrEnum):
    FINDING_NEW = "finding.new"
    FINDING_REOPENED = "finding.reopened"
    FINDING_FIXED = "finding.fixed"
    SCAN_COMPLETED = "scan.completed"
    SCAN_FAILED = "scan.failed"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    # Last attempt failed; retried at next_attempt_at.
    FAILED = "failed"
    # Gave up after TW_WEBHOOK_MAX_ATTEMPTS.
    DEAD = "dead"


class ReportKind(StrEnum):
    JSON = "json"
    CSV = "csv"
    HTML = "html"


class ReportStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"


def values(enum: type[StrEnum]) -> list[str]:
    return [member.value for member in enum]

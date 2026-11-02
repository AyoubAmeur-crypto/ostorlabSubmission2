"""Finding lifecycle (docs/architecture.md section 4.4).

    before                   seen in this scan            absent, plugin completed
    (none)                   new        + finding.new     -
    new / open / reopened    open                         fixed + finding.fixed
    fixed                    reopened   + finding.reopened unchanged

The functions here change one ``Finding`` row and describe what happened as a
:class:`Transition`; ``app.ingest.service`` writes the events and webhooks.
Triage is never changed here. Findings triaged ``false_positive`` still move
through the states (so the history stays truthful) but never notify.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import isoformat
from app.core.contracts import RawResult
from app.models import Finding, Scan
from app.models.enums import FindingEventKind, FindingStatus, Severity, Triage, WebhookEvent

ACTOR = "system:ingest"
ACTIVE_STATUSES = (FindingStatus.NEW, FindingStatus.OPEN, FindingStatus.REOPENED)


@dataclass(slots=True)
class Transition:
    finding: Finding
    outcome: str  # "created" | "seen" | "reopened" | "fixed" (IngestSummary list names)
    events: list[tuple[FindingEventKind, dict[str, Any]]] = field(default_factory=list)
    webhook: WebhookEvent | None = None


def _notify(finding: Finding, event: WebhookEvent) -> WebhookEvent | None:
    return None if finding.triage == Triage.FALSE_POSITIVE else event


def create(
    scan: Scan, result: RawResult, fingerprint: str, severity: Severity, at: datetime
) -> Transition:
    finding = Finding(
        tenant_id=scan.tenant_id, asset_id=scan.asset_id, plugin=result.plugin,
        rule_id=result.rule_id, fingerprint=fingerprint, title=result.title[:300],
        severity=severity.value, status=FindingStatus.NEW.value, triage=Triage.NONE.value,
        first_seen_at=at, last_seen_at=at, location=result.location,
        evidence=dict(result.evidence), scan_id_last=scan.id,
    )
    event = (FindingEventKind.CREATED, {"scan_id": scan.id, "severity": severity.value})
    return Transition(finding, "created", [event], WebhookEvent.FINDING_NEW)


def observe(
    finding: Finding, scan: Scan, result: RawResult, severity: Severity, at: datetime
) -> Transition:
    """``finding`` (same fingerprint) showed up again in ``scan``."""
    events: list[tuple[FindingEventKind, dict[str, Any]]] = []
    if finding.severity != severity:
        change = {"scan_id": scan.id, "from": finding.severity, "to": severity.value}
        events.append((FindingEventKind.SEVERITY_CHANGED, change))
        finding.severity = severity.value
    finding.title = result.title[:300]
    finding.location = result.location
    finding.evidence = dict(result.evidence)
    finding.last_seen_at = at
    finding.scan_id_last = scan.id
    if finding.status == FindingStatus.FIXED:
        previous = isoformat(finding.fixed_at) if finding.fixed_at else None
        events.append((FindingEventKind.REOPENED, {"scan_id": scan.id, "fixed_at": previous}))
        finding.status = FindingStatus.REOPENED.value
        finding.fixed_at = None
        webhook = _notify(finding, WebhookEvent.FINDING_REOPENED)
        return Transition(finding, "reopened", events, webhook)
    finding.status = FindingStatus.OPEN.value
    return Transition(finding, "seen", events)


def resolve(finding: Finding, scan: Scan, at: datetime) -> Transition:
    """``finding`` was absent from a completed run of its plugin."""
    finding.status = FindingStatus.FIXED.value
    finding.fixed_at = at
    event = (FindingEventKind.FIXED, {"scan_id": scan.id})
    return Transition(finding, "fixed", [event], _notify(finding, WebhookEvent.FINDING_FIXED))


def absent_findings(
    session: Session, scan: Scan, completed_plugins: Collection[str], seen_ids: Collection[int]
) -> list[Finding]:
    """Active findings of the scan's asset, from plugins that completed in this
    scan, that the scan did not report. A plugin that failed, timed out or had
    no data is not in ``completed_plugins``, so its findings stay as they are."""
    if not completed_plugins:
        return []
    stmt = (
        select(Finding)
        .where(
            Finding.tenant_id == scan.tenant_id,
            Finding.asset_id == scan.asset_id,
            Finding.plugin.in_(sorted(completed_plugins)),
            Finding.status.in_([s.value for s in ACTIVE_STATUSES]),
        )
        .order_by(Finding.id)
    )
    if seen_ids:
        stmt = stmt.where(Finding.id.not_in(list(seen_ids)))
    return list(session.scalars(stmt))

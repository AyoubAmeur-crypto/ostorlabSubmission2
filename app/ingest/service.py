"""Turn one scan's raw plugin results into findings (architecture section 2, step 4).

Called by the worker pipeline inside the scan job's transaction, with only the
results of the plugins that completed. Flushes, never commits: findings,
finding events and outbox rows commit together with the scan status.

Two scans of the same asset ingesting at the same moment can both try to
insert a new fingerprint; the ``(tenant_id, fingerprint)`` unique constraint
then fails the later job, whose retry sees the row and treats it as seen.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.contracts import IngestSummary, RawResult
from app.core.errors import Conflict, ValidationFailed
from app.ingest import lifecycle, suppression
from app.ingest.fingerprint import compute_fingerprint
from app.ingest.severity import map_severity
from app.models import Finding, FindingEvent, Scan
from app.models.enums import ScanStatus
from app.schemas.webhook import FindingEventData

log = logging.getLogger(__name__)

INGESTIBLE_STATUSES = frozenset({ScanStatus.RUNNING, ScanStatus.COMPLETED})


def ingest_scan_results(
    session: Session,
    scan: Scan,
    results: Sequence[RawResult],
    *,
    completed_plugins: Sequence[str],
) -> IngestSummary:
    if scan.status not in INGESTIBLE_STATUSES:
        raise Conflict(f"scan {scan.id} is {scan.status}; only running scans are ingested")
    completed = set(completed_plugins)
    summary = IngestSummary(scan_id=scan.id)
    batch = _dedupe(scan, results, completed, summary)
    at = clock.now()

    existing: dict[str, Finding] = {}
    if batch:
        # Serialize with manual triage (the API uses the same row lock) and
        # refresh cached objects before deciding whether rules may set triage.
        stmt = (
            select(Finding)
            .where(Finding.tenant_id == scan.tenant_id, Finding.fingerprint.in_(list(batch)))
            .order_by(Finding.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        existing = {f.fingerprint: f for f in session.scalars(stmt)}

    transitions: list[lifecycle.Transition] = []
    for fingerprint, result in batch.items():
        severity = map_severity(result.plugin, result.severity)
        finding = existing.get(fingerprint)
        if finding is None:
            transition = lifecycle.create(scan, result, fingerprint, severity, at)
            session.add(transition.finding)
        else:
            transition = lifecycle.observe(finding, scan, result, severity, at)
        transitions.append(transition)
    session.flush()

    seen_ids = [t.finding.id for t in transitions]
    for finding in lifecycle.absent_findings(session, scan, completed, seen_ids):
        transitions.append(lifecycle.resolve(finding, scan, at))
    suppression.apply_rules(session, transitions)
    _record(session, transitions, summary, at)

    # nested: "created" is a reserved LogRecord attribute and cannot be an extra key
    fields = {"scan_id": scan.id, "tenant_id": scan.tenant_id, "counts": summary.counts()}
    log.info("ingest.completed", extra=fields)
    return summary


def _dedupe(
    scan: Scan, results: Sequence[RawResult], completed: Collection[str], summary: IngestSummary
) -> dict[str, RawResult]:
    """Fingerprint -> first result with it. Later duplicates are counted, not stored."""
    batch: dict[str, RawResult] = {}
    for result in results:
        if result.asset_id != scan.asset_id:
            raise ValidationFailed(f"result for asset {result.asset_id} passed to scan {scan.id}")
        if result.plugin not in completed:
            raise ValidationFailed(f"{result.plugin} is not in completed_plugins")
        fingerprint = compute_fingerprint(
            scan.tenant_id, scan.asset_id, result.plugin, result.rule_id, result.location
        )
        if fingerprint in batch:
            summary.duplicates += 1
            continue
        batch[fingerprint] = result
    return batch


def _record(
    session: Session,
    transitions: list[lifecycle.Transition],
    summary: IngestSummary,
    at: datetime,
) -> None:
    # Imported at call time so that importing ingest does not load the
    # reporting package (and so tests can substitute the outbox).
    from app.reporting.outbox import enqueue_event

    for transition in transitions:
        finding = transition.finding
        getattr(summary, transition.outcome).append(finding.id)
        for kind, data in transition.events:
            session.add(
                FindingEvent(
                    finding_id=finding.id, at=at, actor=lifecycle.ACTOR, kind=kind.value, data=data
                )
            )
        if transition.webhook is not None:
            payload = FindingEventData.from_finding(finding).model_dump(mode="json")
            enqueue_event(session, finding.tenant_id, transition.webhook, payload)
    session.flush()

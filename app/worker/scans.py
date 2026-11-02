"""Scan requests and the scan state machine: queued -> running -> completed |
failed; queued | running -> cancelled (API); queued -> failed (job died early).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import Conflict, InvalidTransition, NotFound
from app.core.logging import get_logger
from app.models import Asset, JobKind, Scan, ScanProfile, ScanStatus, ScanTrigger, WebhookEvent
from app.schemas.webhook import ScanEventData
from app.worker.queue import enqueue

log = get_logger(__name__)

TRANSITIONS: dict[str, frozenset[str]] = {
    ScanStatus.QUEUED: frozenset({ScanStatus.RUNNING, ScanStatus.CANCELLED, ScanStatus.FAILED}),
    ScanStatus.RUNNING: frozenset({ScanStatus.COMPLETED, ScanStatus.FAILED, ScanStatus.CANCELLED}),
}


def request_scan(
    session: Session,
    *,
    tenant_id: int,
    profile_id: int,
    asset_id: int,
    trigger: ScanTrigger | str,
    scheduled_slot: datetime | None = None,
) -> Scan:
    """Insert a queued scan and its ``scan.run`` job (flushes, does not commit)."""
    profile = session.get(ScanProfile, profile_id)
    if profile is None or profile.tenant_id != tenant_id:
        raise NotFound.for_resource("profile", profile_id)
    asset = session.get(Asset, asset_id)
    if asset is None or asset.tenant_id != tenant_id:
        raise NotFound.for_resource("asset", asset_id)
    if asset.is_archived:
        raise Conflict(f"asset {asset_id} is archived")
    if not profile.enabled:
        raise Conflict(f"profile {profile_id} is disabled")
    scan = Scan(
        tenant_id=tenant_id,
        profile_id=profile_id,
        asset_id=asset_id,
        status=ScanStatus.QUEUED,
        trigger=str(ScanTrigger(trigger)),
        requested_at=clock.now(),
        scheduled_slot=clock.ensure_utc(scheduled_slot) if scheduled_slot else None,
    )
    session.add(scan)
    session.flush()
    job = enqueue(session, JobKind.SCAN_RUN, {"scan_id": scan.id})
    log.info("scan.requested", extra={"scan_id": scan.id, "job_id": job.id, "trigger": trigger})
    return scan


def transition(scan: Scan, target: ScanStatus, *, error: str | None = None) -> None:
    """Apply a state change and its timestamps, or raise :class:`InvalidTransition`."""
    if target not in TRANSITIONS.get(scan.status, frozenset()):
        raise InvalidTransition("scan", scan.status, target)
    now = clock.now()
    scan.status = target
    if target == ScanStatus.RUNNING:
        scan.started_at = scan.started_at or now
    else:
        scan.finished_at = now
    if error is not None:
        scan.error = error


def fail_scan(session: Session, scan_id: int, error: str) -> Scan | None:
    """Fail a scan whose job gave up, and tell the customer. No-op if already finished."""
    scan = session.get(Scan, scan_id, with_for_update=True, populate_existing=True)
    if scan is None or ScanStatus(scan.status).is_terminal:
        return None
    transition(scan, ScanStatus.FAILED, error=error)
    notify(session, scan, WebhookEvent.SCAN_FAILED)
    log.warning("scan.failed", extra={"scan_id": scan.id, "tenant_id": scan.tenant_id})
    return scan


def notify(session: Session, scan: Scan, event: WebhookEvent, counts: dict | None = None) -> None:
    """Queue the scan.completed / scan.failed webhook in the caller's transaction."""
    from app.reporting.outbox import enqueue_event

    session.flush()
    data = ScanEventData.from_scan(scan, counts).model_dump(mode="json")
    enqueue_event(session, scan.tenant_id, event, data)

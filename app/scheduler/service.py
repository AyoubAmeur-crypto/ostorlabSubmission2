"""One scheduler tick: turn due cron slots into scans.

Windows overlap on purpose (lookback 300 s, tick every 30 s) so a slot missed
during a restart is still planned. That is safe because the slot is stored on
the scan behind a unique index, which also settles races between replicas.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import ensure_utc, isoformat
from app.core.config import get_settings
from app.core.errors import ValidationFailed
from app.core.logging import get_logger
from app.models import Asset, Scan, ScanProfile, ScanTrigger, Tenant
from app.scheduler.planner import due_slots, zone
from app.worker.scans import request_scan

log = get_logger(__name__)

SLOT_INDEX = "uq_scans_profile_id_asset_id_scheduled_slot"


def tick(session: Session, now: datetime) -> list[Scan]:
    """Plan every due slot in ``(now - lookback, now]``. Returns the new scans."""
    now = ensure_utc(now)
    window_start = now - timedelta(seconds=get_settings().scheduler_lookback_s)
    rows = session.execute(
        select(ScanProfile, Tenant)
        .join(Tenant, Tenant.id == ScanProfile.tenant_id)
        .where(ScanProfile.enabled.is_(True), ScanProfile.schedule_cron.is_not(None))
        .order_by(ScanProfile.id)
    ).all()
    planned: list[Scan] = []
    for profile, tenant in rows:
        try:
            slots = due_slots(profile.schedule_cron, _tenant_zone(tenant), window_start, now)
        except ValidationFailed as exc:
            fields = {"profile_id": profile.id, "error": exc.message}
            log.warning("scheduler.profile_skipped", extra=fields)
            continue
        if slots:
            planned.extend(_plan_profile(session, profile, slots))
    return planned


def _tenant_zone(tenant: Tenant) -> str:
    try:
        return zone(tenant.timezone).key
    except ValidationFailed:
        log.warning("scheduler.bad_timezone", extra={"tenant_id": tenant.id})
        return get_settings().default_timezone


def _plan_profile(session: Session, profile: ScanProfile, slots: list[datetime]) -> list[Scan]:
    wanted_tags = set((profile.options or {}).get("asset_tags") or [])
    assets = session.scalars(
        select(Asset)
        .where(Asset.tenant_id == profile.tenant_id, Asset.archived_at.is_(None))
        .order_by(Asset.id)
    ).all()
    # Skip slots earlier ticks already planned instead of tripping the unique
    # index every 30 s (each violation is an ERROR line in the Postgres log).
    known = select(Scan.asset_id, Scan.scheduled_slot).where(Scan.profile_id == profile.id)
    existing = set(session.execute(known.where(Scan.scheduled_slot.in_(slots))).tuples())
    planned = []
    for asset in assets:
        if wanted_tags and not wanted_tags.intersection(asset.tags or []):
            continue
        for slot in slots:
            if (asset.id, slot) not in existing and (scan := _plan(session, profile, asset, slot)):
                planned.append(scan)
    return planned


def _plan(session: Session, profile: ScanProfile, asset: Asset, slot: datetime) -> Scan | None:
    try:
        with session.begin_nested():
            ids = {"tenant_id": profile.tenant_id, "profile_id": profile.id, "asset_id": asset.id}
            scan = request_scan(session, **ids, trigger=ScanTrigger.SCHEDULE, scheduled_slot=slot)
    except IntegrityError as exc:
        if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) != SLOT_INDEX:
            raise
        return None  # another scheduler planned this slot first
    fields = {"profile_id": profile.id, "asset_id": asset.id, "scan_id": scan.id}
    log.info("scheduler.scan_planned", extra={**fields, "slot": isoformat(slot)})
    return scan

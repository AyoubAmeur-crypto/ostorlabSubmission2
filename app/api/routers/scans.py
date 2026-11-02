"""Scans: request, list, inspect and cancel.

Creation goes through ``app.worker.scans.request_scan`` (row + ``scan.run`` job).
Cancelling only flips the status; the worker re-reads it and stops without ingesting.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.api.deps import SessionDep, TenantDep, get_owned
from app.api.pagination import PageDep, paginate
from app.core import clock
from app.core.errors import InvalidTransition
from app.core.logging import get_logger
from app.models import Scan, ScanStatus, ScanTrigger
from app.schemas import Page, ScanCreate, ScanListParams, ScanRead

router = APIRouter(tags=["scans"])
log = get_logger(__name__)

CANCELLABLE = frozenset({ScanStatus.QUEUED, ScanStatus.RUNNING})


def scan_filters(
    status: Annotated[list[ScanStatus] | None, Query()] = None,
    asset_id: int | None = None,
    profile_id: int | None = None,
) -> ScanListParams:
    return ScanListParams(status=status, asset_id=asset_id, profile_id=profile_id)


@router.post("/scans", status_code=201)
def create_scan(body: ScanCreate, ctx: TenantDep, session: SessionDep) -> ScanRead:
    from app.worker.scans import request_scan

    scan = request_scan(
        session,
        tenant_id=ctx.tenant_id,
        profile_id=body.profile_id,
        asset_id=body.asset_id,
        trigger=ScanTrigger.API,
    )
    return ScanRead.model_validate(scan)


@router.get("/scans")
def list_scans(
    ctx: TenantDep,
    session: SessionDep,
    page: PageDep,
    filters: Annotated[ScanListParams, Depends(scan_filters)],
) -> Page[ScanRead]:
    stmt = select(Scan).where(Scan.tenant_id == ctx.tenant_id)
    if filters.status:
        stmt = stmt.where(Scan.status.in_(filters.status))
    if filters.asset_id is not None:
        stmt = stmt.where(Scan.asset_id == filters.asset_id)
    if filters.profile_id is not None:
        stmt = stmt.where(Scan.profile_id == filters.profile_id)
    stmt = stmt.order_by(Scan.requested_at.desc(), Scan.id.desc())
    return paginate(session, stmt, page, ScanRead)


@router.get("/scans/{scan_id}")
def get_scan(scan_id: int, ctx: TenantDep, session: SessionDep) -> ScanRead:
    return ScanRead.model_validate(get_owned(session, Scan, scan_id, ctx.tenant_id, "scan"))


@router.post("/scans/{scan_id}/cancel")
def cancel_scan(scan_id: int, ctx: TenantDep, session: SessionDep) -> ScanRead:
    # Row lock: the worker may be moving the same scan to running/completed.
    scan = get_owned(session, Scan, scan_id, ctx.tenant_id, "scan", for_update=True)
    if scan.status not in CANCELLABLE:
        raise InvalidTransition("scan", scan.status, ScanStatus.CANCELLED)
    log.info("scan.cancelled", extra={"scan_id": scan.id, "previous": scan.status})
    scan.status = ScanStatus.CANCELLED
    scan.finished_at = clock.now()
    session.flush()
    return ScanRead.model_validate(scan)

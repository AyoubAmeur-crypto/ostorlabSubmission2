"""Findings: filtered listing (newest ``last_seen_at`` first), detail, audit trail, triage.

Filters combine with AND; a repeated filter (``?severity=high&severity=critical``)
matches any of its values. ``since`` must carry a UTC offset.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Select, select

from app.api.deps import SessionDep, TenantDep, get_owned
from app.api.pagination import PageDep, paginate
from app.core import clock
from app.core.logging import get_logger
from app.models import Finding, FindingEvent, FindingEventKind, FindingStatus, Severity, Triage
from app.schemas import FindingEventRead, FindingFilters, FindingRead, FindingTriageUpdate, Page

router = APIRouter(tags=["findings"])
log = get_logger(__name__)


def finding_filters(
    severity: Annotated[list[Severity] | None, Query()] = None,
    status: Annotated[list[FindingStatus] | None, Query()] = None,
    triage: Annotated[list[Triage] | None, Query()] = None,
    asset_id: int | None = None,
    plugin: str | None = None,
    since: datetime | None = None,
) -> FindingFilters:
    return FindingFilters(
        severity=severity,
        status=status,
        triage=triage,
        asset_id=asset_id,
        plugin=plugin,
        since=since,
    )


def filtered_findings(tenant_id: int, filters: FindingFilters) -> Select[Any]:
    stmt = select(Finding).where(Finding.tenant_id == tenant_id)
    if filters.severity:
        stmt = stmt.where(Finding.severity.in_(filters.severity))
    if filters.status:
        stmt = stmt.where(Finding.status.in_(filters.status))
    if filters.triage:
        stmt = stmt.where(Finding.triage.in_(filters.triage))
    if filters.asset_id is not None:
        stmt = stmt.where(Finding.asset_id == filters.asset_id)
    if filters.plugin is not None:
        stmt = stmt.where(Finding.plugin == filters.plugin)
    if filters.since is not None:
        stmt = stmt.where(Finding.last_seen_at >= filters.since)
    return stmt


@router.get("/findings")
def list_findings(
    ctx: TenantDep,
    session: SessionDep,
    page: PageDep,
    filters: Annotated[FindingFilters, Depends(finding_filters)],
) -> Page[FindingRead]:
    stmt = filtered_findings(ctx.tenant_id, filters)
    stmt = stmt.order_by(Finding.last_seen_at.desc(), Finding.id.desc())
    return paginate(session, stmt, page, FindingRead)


@router.get("/findings/{finding_id}")
def get_finding(finding_id: int, ctx: TenantDep, session: SessionDep) -> FindingRead:
    finding = get_owned(session, Finding, finding_id, ctx.tenant_id, "finding")
    return FindingRead.model_validate(finding)


@router.get("/findings/{finding_id}/events")
def list_finding_events(
    finding_id: int, ctx: TenantDep, session: SessionDep, page: PageDep
) -> Page[FindingEventRead]:
    finding = get_owned(session, Finding, finding_id, ctx.tenant_id, "finding")
    stmt = (
        select(FindingEvent)
        .where(FindingEvent.finding_id == finding.id)
        .order_by(FindingEvent.at, FindingEvent.id)
    )
    return paginate(session, stmt, page, FindingEventRead)


@router.patch("/findings/{finding_id}")
def triage_finding(
    finding_id: int, body: FindingTriageUpdate, ctx: TenantDep, session: SessionDep
) -> FindingRead:
    """Set the triage status. A change writes a ``triage_changed`` event;
    repeating the current value is a no-op."""
    finding = get_owned(session, Finding, finding_id, ctx.tenant_id, "finding", for_update=True)
    previous = finding.triage
    if previous != body.triage:
        data: dict[str, Any] = {"from": previous, "to": body.triage.value}
        if body.note:
            data["note"] = body.note
        session.add(
            FindingEvent(
                finding_id=finding.id,
                at=clock.now(),
                actor=ctx.actor,
                kind=FindingEventKind.TRIAGE_CHANGED,
                data=data,
            )
        )
        finding.triage = body.triage
        session.flush()
        log.info("finding.triaged", extra={"finding_id": finding.id, "triage": body.triage})
    return FindingRead.model_validate(finding)

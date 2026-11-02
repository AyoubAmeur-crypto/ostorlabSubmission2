"""Report requests and generation.

Flow: the API calls request_report() (row ``pending`` + ``report.generate``
job), the worker calls generate_report() which writes
``TW_REPORTS_DIR/<tenant_id>/report-<id>.<kind>`` and marks the row ``ready``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core import clock
from app.core.config import get_settings
from app.core.errors import NotFound, ValidationFailed
from app.core.logging import get_logger
from app.models import Finding, JobKind, Report, ReportKind, ReportStatus, Tenant
from app.reporting.csv_export import write_csv
from app.reporting.html import render_html
from app.reporting.json_export import write_json
from app.schemas.finding import FindingFilters

log = get_logger(__name__)


def request_report(session: Session, tenant_id: int, kind: ReportKind | str,
                   filters: FindingFilters) -> Report:
    # imported here: app.worker imports reporting (outbox pump), avoid the cycle
    from app.worker.queue import enqueue

    try:
        kind = ReportKind(kind)
    except ValueError:
        raise ValidationFailed(f"unknown report kind {kind!r}") from None
    report = Report(tenant_id=tenant_id, kind=kind.value, filters=filters.to_column(),
                    status=ReportStatus.PENDING.value, created_at=clock.now())
    session.add(report)
    session.flush()
    enqueue(session, JobKind.REPORT_GENERATE, {"report_id": report.id})
    log.info("report.requested", extra={"tenant_id": tenant_id, "report_id": report.id,
                                        "kind": kind.value})
    return report


def findings_query(tenant_id: int, filters: FindingFilters) -> Any:
    stmt = (select(Finding).options(joinedload(Finding.asset))
            .where(Finding.tenant_id == tenant_id))
    if filters.severity:
        stmt = stmt.where(Finding.severity.in_([s.value for s in filters.severity]))
    if filters.status:
        stmt = stmt.where(Finding.status.in_([s.value for s in filters.status]))
    if filters.triage:
        stmt = stmt.where(Finding.triage.in_([t.value for t in filters.triage]))
    if filters.asset_id is not None:
        stmt = stmt.where(Finding.asset_id == filters.asset_id)
    if filters.plugin:
        stmt = stmt.where(Finding.plugin == filters.plugin)
    if filters.since is not None:
        stmt = stmt.where(Finding.last_seen_at >= filters.since)
    return stmt.order_by(Finding.id)


def generate_report(session: Session, report_id: int) -> str:
    """Write the report file and mark the row ready. Returns the file path.

    Errors propagate so the worker retries the job; the row stays ``pending``
    because the job's transaction is rolled back. Running it again on a
    ``ready`` report just returns the existing path (job replays).
    """
    report = session.get(Report, report_id)
    if report is None:
        raise NotFound.for_resource("report", report_id)
    if report.status == ReportStatus.READY and report.path:
        return report.path
    tenant = session.get(Tenant, report.tenant_id)
    filters = FindingFilters.model_validate(report.filters or {})
    findings = session.scalars(findings_query(report.tenant_id, filters)).all()
    now = clock.now()

    out_dir = get_settings().reports_dir / str(report.tenant_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"report-{report.id}.{report.kind}"
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        if report.kind == ReportKind.CSV:
            count = write_csv(findings, fh)
        elif report.kind == ReportKind.JSON:
            count = write_json(fh, tenant, findings, filters=report.filters, generated_at=now)
        elif report.kind == ReportKind.HTML:
            doc, count = render_html(tenant, findings, filters=report.filters, generated_at=now)
            fh.write(doc)
        else:
            raise ValidationFailed(f"unknown report kind {report.kind!r}")
    tmp.replace(path)

    report.status = ReportStatus.READY.value
    report.path = str(path)
    report.error = None
    report.finished_at = now
    session.flush()
    log.info("report.ready", extra={"tenant_id": report.tenant_id, "report_id": report.id,
                                    "kind": report.kind, "findings": count})
    return str(path)

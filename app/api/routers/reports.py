"""Reports: request a findings export, poll it, download it once ready.

``app.reporting.service.request_report`` stores a ``pending`` row and a job; the
worker writes ``TW_REPORTS_DIR/<tenant_id>/report-<id>.<kind>`` and marks it ``ready``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.api.deps import SessionDep, TenantDep, get_owned
from app.core.config import get_settings
from app.core.errors import Conflict, NotFound
from app.core.logging import get_logger
from app.models import Asset, Report, ReportKind, ReportStatus
from app.schemas import ReportCreate, ReportRead

router = APIRouter(tags=["reports"])
log = get_logger(__name__)

MEDIA_TYPES = {
    ReportKind.JSON: "application/json",
    ReportKind.CSV: "text/csv; charset=utf-8",
    ReportKind.HTML: "text/html; charset=utf-8",
}


@router.post("/reports", status_code=201)
def create_report(body: ReportCreate, ctx: TenantDep, session: SessionDep) -> ReportRead:
    from app.reporting.service import request_report

    if body.filters.asset_id is not None:
        get_owned(session, Asset, body.filters.asset_id, ctx.tenant_id, "asset")
    report = request_report(session, ctx.tenant_id, body.kind, body.filters)
    log.info("report.requested", extra={"tenant_id": ctx.tenant_id, "report_id": report.id})
    return ReportRead.model_validate(report)


@router.get("/reports/{report_id}")
def get_report(report_id: int, ctx: TenantDep, session: SessionDep) -> ReportRead:
    return ReportRead.model_validate(get_owned(session, Report, report_id, ctx.tenant_id, "report"))


@router.get("/reports/{report_id}/download", response_class=FileResponse)
def download_report(report_id: int, ctx: TenantDep, session: SessionDep) -> FileResponse:
    report = get_owned(session, Report, report_id, ctx.tenant_id, "report")
    if report.status != ReportStatus.READY or not report.path:
        raise Conflict("report is not ready", details={"status": report.status})
    # Serve only from this tenant's directory, whatever the row says.
    tenant_dir = (get_settings().reports_dir / str(ctx.tenant_id)).resolve()
    path = Path(report.path).resolve()
    if not path.is_relative_to(tenant_dir) or not path.is_file():
        log.warning(
            "report.file_unavailable", extra={"tenant_id": ctx.tenant_id, "report_id": report.id}
        )
        raise NotFound.for_resource("report file", report.id)
    kind = ReportKind(report.kind)
    return FileResponse(path, media_type=MEDIA_TYPES[kind], filename=f"report-{report.id}.{kind}")

"""Runs claimed jobs. One job is three short transactions: ``claim``; the
handler plus ``mark_done`` (findings, events, outbox rows and ``done`` commit
together); on error ``mark_failed``. Scans also commit ``running`` up front so
the API shows progress while plugins run.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.core.contracts import AssetSnapshot, PluginContext, RawResult
from app.core.db import session_scope
from app.core.errors import NotFound, PluginError, TidewrightError, UnknownPlugin, ValidationFailed
from app.core.logging import bind, get_logger
from app.models import Job, Report, Scan, Tenant
from app.models.enums import JobKind, JobStatus, ReportStatus, ScanStatus, WebhookEvent
from app.worker.heartbeat import heartbeat
from app.worker.queue import LostJobLock, claim, mark_done, mark_failed
from app.worker.scans import fail_scan, notify, transition

log = get_logger(__name__)

PERMANENT_ERRORS = (NotFound, ValidationFailed)  # no retry can fix these: bury at once


def run_one(worker_id: str) -> int | None:
    """Claim and run at most one due job. Returns its id, None if nothing was due."""
    with session_scope() as session:
        job = claim(session, worker_id)
        if job is None:
            return None
        job_id, kind, payload, attempt = job.id, job.kind, dict(job.payload), job.attempts
    with bind(job_id=job_id, job_kind=kind):
        log.info("job.claimed", extra={"worker": worker_id, "attempt": attempt})
        try:
            with heartbeat(job_id, worker_id), session_scope() as session:
                handler = HANDLERS.get(kind)
                if handler is None:
                    raise ValidationFailed(f"unknown job kind {kind!r}")
                handler(session, payload)
                mark_done(session, job_id, worker_id)
            log.info("job.done")
        except LostJobLock:
            log.warning("job.lock_lost", extra={"worker": worker_id})
        except Exception as exc:
            _record_failure(job_id, worker_id, exc)
    return job_id


def _record_failure(job_id: int, worker_id: str, exc: Exception) -> None:
    known = isinstance(exc, TidewrightError)
    message = exc.message if known else f"{type(exc).__name__}: {exc}"
    log.warning("job.failed", exc_info=not known, extra={"error": message})
    with session_scope() as session:
        permanent = isinstance(exc, PERMANENT_ERRORS)
        job = mark_failed(session, job_id, worker_id, message, permanent=permanent)
        if job is None:
            log.warning("job.lock_lost", extra={"worker": worker_id})
        elif job.status == JobStatus.DEAD:
            handle_dead_job(session, job)
        else:
            log.info("job.retry_scheduled", extra={"run_after": clock.isoformat(job.run_after)})


def handle_dead_job(session: Session, job: Job) -> None:
    """A dead job's subject will never finish: mark it failed so nobody waits on it."""
    log.error("job.dead", extra={"job_id": job.id, "kind": job.kind, "attempts": job.attempts})
    error = job.last_error or "job gave up"
    subject_id = job.payload.get("scan_id") or job.payload.get("report_id")
    if not isinstance(subject_id, int):
        return
    if job.kind == JobKind.SCAN_RUN:
        fail_scan(session, subject_id, error)
    elif job.kind == JobKind.REPORT_GENERATE:
        report = session.get(Report, subject_id, with_for_update=True)
        if report is not None and report.status not in (ReportStatus.READY, ReportStatus.FAILED):
            report.status = ReportStatus.FAILED
            report.error = error
            report.finished_at = clock.now()
            session.flush()


def _payload_id(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int):
        raise ValidationFailed(f"job payload has no integer {key!r}")
    return value


def run_scan(session: Session, scan_id: int) -> Scan | None:
    """Run the plugins, ingest, finish. None when finished or cancelled already."""
    from app.plugins.registry import get_plugin, run_with_timeout

    if not _start(scan_id):
        return None
    scan = session.get_one(Scan, scan_id)
    snapshot = AssetSnapshot.from_models(scan.asset, session.get_one(Tenant, scan.tenant_id))
    profile = scan.profile
    context = PluginContext.default(scan_id=scan_id)
    timeout_s = float((profile.options or {}).get("timeout_s") or get_settings().plugin_timeout_s)
    results, completed, errors = [], [], []
    for name in profile.plugins:
        if session.scalar(select(Scan.status).where(Scan.id == scan_id)) == ScanStatus.CANCELLED:
            log.info("scan.cancel_observed", extra={"scan_id": scan_id, "next_plugin": name})
            return None
        try:
            options = profile.plugin_options(name)
            found = run_with_timeout(get_plugin(name), snapshot, options, context, timeout_s)
        except (PluginError, UnknownPlugin) as exc:
            errors.append(exc.message)
            fields = {"scan_id": scan_id, "plugin": name, "code": exc.code}
            log.warning("plugin.failed", extra=fields)
            continue
        results.extend(found)
        completed.append(name)
    return _finish(session, scan_id, results, completed, errors)


def _start(scan_id: int) -> bool:
    with session_scope() as session:
        scan = session.get(Scan, scan_id, with_for_update=True)
        if scan is None:
            raise NotFound.for_resource("scan", scan_id)
        if scan.status not in (ScanStatus.QUEUED, ScanStatus.RUNNING):
            log.info("scan.skipped", extra={"scan_id": scan_id, "status": scan.status})
            return False
        # Still running: an earlier attempt failed or was reaped. Plugins are
        # deterministic, so the retry simply starts over.
        log.info("scan.started", extra={"scan_id": scan_id, "resumed": scan.status != "queued"})
        if scan.status == ScanStatus.QUEUED:
            transition(scan, ScanStatus.RUNNING)
        return True


def _finish(
    session: Session, scan_id: int, results: list[RawResult], done: list[str], errors: list[str]
) -> Scan | None:
    from app.ingest.service import ingest_scan_results

    # Lock the row: an API cancel either committed before (we see it here)
    # or waits for our commit and then finds a finished scan.
    scan = session.get_one(Scan, scan_id, with_for_update=True, populate_existing=True)
    if scan.status == ScanStatus.CANCELLED:
        log.info("scan.cancel_observed", extra={"scan_id": scan_id})
        return None
    counts: dict[str, int] = {}
    if done:
        summary = ingest_scan_results(session, scan, results, completed_plugins=done)
        counts = summary.counts()
        transition(scan, ScanStatus.COMPLETED, error="; ".join(errors) or None)
        event = WebhookEvent.SCAN_COMPLETED
    else:
        transition(scan, ScanStatus.FAILED, error="; ".join(errors) or "profile has no plugins")
        event = WebhookEvent.SCAN_FAILED
    notify(session, scan, event, counts)
    log.info("scan.finished", extra={"scan_id": scan_id, "status": scan.status, "counts": counts})
    return scan


def _run_report(session: Session, payload: dict[str, Any]) -> None:
    from app.reporting.service import generate_report

    report_id = _payload_id(payload, "report_id")
    log.info("report.generated", extra={"path": generate_report(session, report_id)})


HANDLERS: dict[str, Callable[[Session, dict[str, Any]], object]] = {
    JobKind.SCAN_RUN: lambda session, payload: run_scan(session, _payload_id(payload, "scan_id")),
    JobKind.REPORT_GENERATE: _run_report,
}

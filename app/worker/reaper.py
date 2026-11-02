"""Takes back jobs whose worker died mid-run (OOM kill, node loss).

A running job with ``heartbeat_at`` older than ``TW_WORKER_STALE_AFTER_S``
counts as a failed attempt (``failed`` with backoff, or ``dead``). A worker
that was only slow gets ``LostJobLock`` from ``mark_done`` and rolls back.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models import Job, JobStatus
from app.worker.retry import apply_failure

log = get_logger(__name__)


def reap_stale(
    session: Session, *, now: datetime | None = None, stale_after_s: float | None = None
) -> list[int]:
    """Requeue (or bury) stale running jobs. Returns their ids."""
    from app.worker.runner import handle_dead_job

    now = now or clock.now()
    threshold = get_settings().worker_stale_after_s if stale_after_s is None else stale_after_s
    cutoff = now - timedelta(seconds=threshold)
    last_sign_of_life = func.coalesce(Job.heartbeat_at, Job.locked_at, Job.created_at)
    stale = session.scalars(
        select(Job)
        .where(Job.status == JobStatus.RUNNING, last_sign_of_life < cutoff)
        .order_by(Job.id)
        .with_for_update(skip_locked=True)
    ).all()
    for job in stale:
        worker = job.locked_by
        apply_failure(job, f"worker {worker} stopped heartbeating", now)
        log.warning(
            "job.reaped",
            extra={"job_id": job.id, "kind": job.kind, "worker": worker, "status": job.status},
        )
        if job.status == JobStatus.DEAD:
            handle_dead_job(session, job)
    session.flush()
    return [job.id for job in stale]

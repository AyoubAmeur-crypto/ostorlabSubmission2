"""Postgres-backed job queue (docs/adr/0001-postgres-queue.md).

``claim`` uses ``FOR UPDATE SKIP LOCKED``: a row another worker is claiming is
skipped, not waited on, and once that worker commits it is ``running`` and no
longer matches. ``locked_by`` stays after the job ends (who ran it last).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.core.errors import Conflict
from app.core.logging import get_logger
from app.models import Job, JobKind, JobStatus
from app.worker.retry import apply_failure

log = get_logger(__name__)

CLAIMABLE = (JobStatus.QUEUED, JobStatus.FAILED)


class LostJobLock(Conflict):
    """The job is no longer running under this worker (the reaper took it back)."""

    code = "lost_job_lock"


def enqueue(
    session: Session,
    kind: JobKind | str,
    payload: dict[str, Any],
    *,
    run_after: datetime | None = None,
    max_attempts: int | None = None,
) -> Job:
    job = Job(
        kind=str(kind),
        payload=dict(payload),
        max_attempts=max_attempts or get_settings().job_max_attempts,
        run_after=clock.ensure_utc(run_after) if run_after else clock.now(),
    )
    session.add(job)
    session.flush()
    return job


def claim(session: Session, worker_id: str) -> Job | None:
    """Lock the next due job for ``worker_id``; the caller commits right away."""
    now = clock.now()
    job = session.scalars(
        select(Job)
        .where(Job.status.in_(CLAIMABLE), Job.run_after <= now)
        .order_by(Job.run_after, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if job is None:
        return None
    job.status = JobStatus.RUNNING
    job.locked_by = worker_id
    job.locked_at = job.heartbeat_at = now
    job.attempts += 1
    session.flush()
    return job


def _owned(session: Session, job_id: int, worker_id: str) -> Job | None:
    job = session.get(Job, job_id, with_for_update=True, populate_existing=True)
    held = job is not None and job.status == JobStatus.RUNNING and job.locked_by == worker_id
    return job if held else None


def mark_done(session: Session, job_id: int, worker_id: str) -> Job:
    """Raises :class:`LostJobLock` if the job was reaped meanwhile, which rolls
    back the handler's work: the job's new owner redoes it."""
    job = _owned(session, job_id, worker_id)
    if job is None:
        raise LostJobLock(f"job {job_id} is no longer held by {worker_id}")
    job.status = JobStatus.DONE
    job.finished_at = clock.now()
    job.last_error = None
    session.flush()
    return job


def mark_failed(
    session: Session, job_id: int, worker_id: str, error: str, *, permanent: bool = False
) -> Job | None:
    """Schedule a retry or bury the job. None when the job was reaped meanwhile."""
    job = _owned(session, job_id, worker_id)
    if job is None:
        return None
    apply_failure(job, error, clock.now(), permanent=permanent)
    session.flush()
    return job

"""Keeps ``jobs.heartbeat_at`` fresh while a job runs, so the reaper leaves it alone."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import update

from app.core import clock
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.logging import get_logger
from app.models import Job, JobStatus

log = get_logger(__name__)


def beat(job_id: int, worker_id: str) -> bool:
    """One heartbeat in its own transaction. False when the job is no longer ours."""
    with session_scope() as session:
        result = session.execute(
            update(Job)
            .where(Job.id == job_id, Job.locked_by == worker_id, Job.status == JobStatus.RUNNING)
            .values(heartbeat_at=clock.now())
        )
    return result.rowcount == 1


@contextmanager
def heartbeat(job_id: int, worker_id: str, interval_s: float | None = None) -> Iterator[None]:
    """Call :func:`beat` every ``TW_WORKER_HEARTBEAT_S`` from a thread while the block runs."""
    interval = interval_s or get_settings().worker_heartbeat_s
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(interval):
            try:
                if not beat(job_id, worker_id):
                    log.warning("job.heartbeat_lost", extra={"job_id": job_id})
                    return
            except Exception:
                # A database blip must not kill the job: the reaper waits several beats.
                log.exception("job.heartbeat_failed", extra={"job_id": job_id})

    thread = threading.Thread(target=loop, name=f"heartbeat-{job_id}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=interval)

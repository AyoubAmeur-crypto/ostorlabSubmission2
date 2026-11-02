"""Retry policy for failed jobs: exponential backoff with a cap, no jitter.

    delay(attempts) = min(TW_RETRY_BASE_S * 2 ** (attempts - 1), TW_RETRY_MAX_S)

Defaults (5 s, 600 s cap, 5 attempts): retries after 5, 10, 20, 40 s, then
``dead``. No jitter on purpose: on-call can tell from ``attempts`` when a job
runs next, and workers poll rather than wake up together.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.core.config import get_settings
from app.models import Job, JobStatus

MAX_ERROR_CHARS = 2000


def backoff_s(attempts: int, *, base_s: float | None = None, max_s: float | None = None) -> float:
    """Seconds to wait after the ``attempts``-th failed attempt (1-based)."""
    settings = get_settings()
    base = settings.retry_base_s if base_s is None else base_s
    cap = settings.retry_max_s if max_s is None else max_s
    exponent = min(max(attempts - 1, 0), 32)  # 2**32 * base is far past any cap
    return min(base * 2**exponent, cap)


def apply_failure(job: Job, error: str, now: datetime, *, permanent: bool = False) -> None:
    """Move a running job to ``failed`` (retry later) or ``dead``.

    ``permanent`` errors (the job can never succeed, e.g. its scan was deleted)
    skip the remaining attempts.
    """
    job.last_error = error[:MAX_ERROR_CHARS]
    if permanent or job.attempts >= job.max_attempts:
        job.status = JobStatus.DEAD
        job.finished_at = now
    else:
        job.status = JobStatus.FAILED
        job.run_after = now + timedelta(seconds=backoff_s(job.attempts))

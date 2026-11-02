import time
from datetime import timedelta

import pytest
from sqlalchemy.exc import OperationalError

from app.models import Job
from app.worker import heartbeat as heartbeat_module
from app.worker.reaper import reap_stale
from app.worker.retry import apply_failure, backoff_s
from tests.factories import TODAY


@pytest.mark.parametrize(
    ("attempts", "delay"), [(0, 5), (1, 5), (2, 10), (4, 40), (8, 600), (999, 600)]
)
def test_backoff_doubles_up_to_the_cap(attempts, delay):
    assert backoff_s(attempts, base_s=5, max_s=600) == delay


def test_permanent_failure_skips_remaining_attempts():
    job = Job(status="running", attempts=1, max_attempts=5)
    apply_failure(job, "x" * 5000, TODAY, permanent=True)
    assert (job.status, job.finished_at, len(job.last_error)) == ("dead", TODAY, 2000)


def test_reaps_only_running_jobs_with_old_heartbeats(db_session, factory, frozen_clock):
    stale, fresh = (
        factory.job(status="running", attempts=1, locked_by="w-1", heartbeat_at=TODAY - age)
        for age in (timedelta(seconds=61), timedelta(seconds=30))
    )
    factory.job(run_after=TODAY - timedelta(hours=1))
    assert reap_stale(db_session) == [stale.id]
    assert (stale.status, stale.run_after) == ("failed", TODAY + timedelta(seconds=5))
    assert "w-1 stopped heartbeating" in stale.last_error
    assert fresh.status == "running"


def test_reaped_scan_job_without_attempts_left_fails_its_scan(
    db_session, factory, frozen_clock, fakes
):
    scan = factory.scan(status="running", started_at=TODAY - timedelta(minutes=10))
    job = factory.job(
        payload={"scan_id": scan.id},
        status="running",
        attempts=5,
        heartbeat_at=TODAY - timedelta(minutes=5),
    )
    reap_stale(db_session)
    assert (job.status, scan.status, scan.finished_at) == ("dead", "failed", TODAY)
    assert [(event, data["status"]) for event, data in fakes.events] == [("scan.failed", "failed")]


def test_heartbeat_survives_a_database_error(monkeypatch):
    # 1.9.2: one failed beat (e.g. during a reconnect) used to end the thread.
    calls = []

    def flaky_beat(job_id, worker_id):
        calls.append(job_id)
        if len(calls) == 1:
            raise OperationalError("UPDATE jobs", {}, Exception("server closed the connection"))
        return True

    monkeypatch.setattr(heartbeat_module, "beat", flaky_beat)
    deadline = time.monotonic() + 2
    with heartbeat_module.heartbeat(7, "w-1", interval_s=0.01):
        while len(calls) < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
    assert calls[:3] == [7, 7, 7]

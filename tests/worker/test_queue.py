from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.core.db import session_scope
from app.models import JobKind
from app.worker.heartbeat import beat
from app.worker.queue import LostJobLock, claim, enqueue, mark_done, mark_failed
from app.worker.reaper import reap_stale
from tests.factories import TODAY, Factory


def test_claim_takes_due_jobs_oldest_first(db_session, factory, frozen_clock):
    later = enqueue(db_session, JobKind.SCAN_RUN, {}, run_after=TODAY - timedelta(seconds=10))
    assert (later.status, later.attempts, later.max_attempts) == ("queued", 0, 5)
    first = factory.job(status="failed", attempts=1, run_after=TODAY - timedelta(minutes=5))
    factory.job(run_after=TODAY + timedelta(seconds=1))
    for status in ("running", "done", "dead"):
        factory.job(status=status, run_after=TODAY - timedelta(hours=1))
    job = claim(db_session, "w1")
    assert (job.id, job.status, job.attempts, job.locked_by) == (first.id, "running", 2, "w1")
    assert job.locked_at == job.heartbeat_at == TODAY
    assert claim(db_session, "w1").id == later.id
    assert claim(db_session, "w1") is None


def test_failed_job_backs_off_then_dies(db_session, factory, frozen_clock):
    job = factory.job(max_attempts=2)
    claim(db_session, "w1")
    mark_failed(db_session, job.id, "w1", "boom")
    assert (job.status, job.run_after) == ("failed", TODAY + timedelta(seconds=5))
    assert claim(db_session, "w1") is None
    frozen_clock.advance(seconds=5)
    claim(db_session, "w1")
    mark_failed(db_session, job.id, "w1", "boom again")
    assert (job.status, job.attempts, job.finished_at) == ("dead", 2, frozen_clock.now())


def test_worker_that_lost_its_job_cannot_finish_it(db_session, factory, frozen_clock):
    job = factory.job()
    claim(db_session, "w1")
    assert beat(job.id, "w1") and not beat(job.id, "w2")
    frozen_clock.advance(minutes=2)
    assert reap_stale(db_session) == [job.id]
    assert not beat(job.id, "w1")
    with pytest.raises(LostJobLock):
        mark_done(db_session, job.id, "w1")
    assert mark_failed(db_session, job.id, "w1", "late") is None


@pytest.mark.committed
def test_claim_skips_rows_locked_by_another_worker(committed_session, frozen_clock):
    factory = Factory(committed_session)
    first = factory.job(run_after=TODAY - timedelta(minutes=2))
    second = factory.job(run_after=TODAY - timedelta(minutes=1))
    committed_session.commit()
    with session_scope() as a, session_scope() as b:
        b.execute(text("SET LOCAL lock_timeout = '2s'"))  # fail fast instead of hanging
        assert claim(a, "a").id == first.id  # row lock held until a commits
        assert claim(b, "b").id == second.id


@pytest.mark.committed
def test_concurrent_workers_claim_each_job_exactly_once(committed_session, frozen_clock):
    factory = Factory(committed_session)
    expected = sorted(factory.job(payload={"n": n}).id for n in range(20))
    committed_session.commit()

    def drain(name: str) -> list[int]:
        claimed = []
        while True:
            with session_scope() as session:
                if (job := claim(session, name)) is None:
                    return claimed
                claimed.append(job.id)

    with ThreadPoolExecutor(max_workers=4) as pool:
        per_worker = list(pool.map(drain, ["w1", "w2", "w3", "w4"], timeout=30))
    assert sorted(sum(per_worker, [])) == expected

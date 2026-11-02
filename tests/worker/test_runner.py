from datetime import timedelta

from sqlalchemy import update

from app.core.errors import PluginTimeout
from app.models import Scan
from app.worker.__main__ import main
from app.worker.runner import run_one
from tests.factories import TODAY


def queue_scan(factory, plugins=("http_headers", "tls_config"), **job):
    tenant = factory.tenant()
    profile = factory.profile(tenant, plugins=list(plugins))
    scan = factory.scan(asset=factory.asset(tenant), profile=profile)
    return scan, factory.job(payload={"scan_id": scan.id}, **job)


def reload(session, *rows):
    for row in rows:
        session.refresh(row)


def test_scan_runs_every_plugin_ingests_and_completes(db_session, factory, fakes, frozen_clock):
    scan, job = queue_scan(factory)
    assert run_one("w1") == job.id
    reload(db_session, scan, job)
    assert (scan.status, scan.started_at, scan.finished_at, job.status) == (
        "completed", TODAY, TODAY, "done")
    assert fakes.ingested == [(scan.id, ["missing-hsts"], ["http_headers", "tls_config"])]
    assert [(e, data["scan_id"]) for e, data in fakes.events] == [("scan.completed", scan.id)]


def test_failed_plugin_is_recorded_and_left_out_of_ingest(db_session, factory, fakes):
    fakes.plugins["tls_config"] = PluginTimeout("tls_config", 20)
    scan, _ = queue_scan(factory, plugins=("tls_config", "http_headers"))
    run_one("w1")
    reload(db_session, scan)
    assert (scan.status, scan.error) == ("completed", "tls_config: timed out after 20s")
    assert fakes.ingested[0][2] == ["http_headers"]


def test_scan_fails_when_no_plugin_completes(db_session, factory, fakes):
    scan, job = queue_scan(factory, plugins=("nope",))
    run_one("w1")
    reload(db_session, scan, job)
    assert (scan.status, scan.error, job.status) == ("failed", "unknown plugin 'nope'", "done")
    assert (fakes.ingested, [event for event, _ in fakes.events]) == ([], ["scan.failed"])


def test_cancel_is_honoured_between_plugins(db_session, factory, fakes):
    scan, job = queue_scan(factory)

    def cancel_after_first(name: str) -> None:
        if name == "http_headers":
            db_session.execute(update(Scan).where(Scan.id == scan.id).values(status="cancelled"))

    fakes.before_plugin = cancel_after_first
    run_one("w1")
    reload(db_session, scan, job)
    assert (scan.status, job.status, fakes.ran) == ("cancelled", "done", ["http_headers"])
    assert fakes.ingested == fakes.events == []
    _, again = queue_scan(factory)  # a scan cancelled while queued never starts
    db_session.execute(update(Scan).values(status="cancelled"))
    run_one("w1")
    reload(db_session, again)
    assert (again.status, fakes.ran) == ("done", ["http_headers"])


def test_error_retries_then_dead_job_fails_the_scan(db_session, factory, fakes, frozen_clock):
    fakes.ingest_error = RuntimeError("connection reset")
    scan, job = queue_scan(factory, max_attempts=2)
    run_one("w1")
    reload(db_session, scan, job)
    assert (job.status, job.last_error) == ("failed", "RuntimeError: connection reset")
    assert (job.run_after, scan.status) == (TODAY + timedelta(seconds=5), "running")
    frozen_clock.advance(seconds=5)
    run_one("w1")
    reload(db_session, scan, job)
    assert (job.status, scan.status, scan.error) == ("dead", "failed", job.last_error)
    assert [event for event, _ in fakes.events] == ["scan.failed"]


def test_report_job_and_dead_report(db_session, factory, fakes):
    report = factory.report()
    ok = factory.job("report.generate", {"report_id": report.id})
    bad = factory.job("report.generate", {"report_id": report.id}, max_attempts=1)
    run_one("w1")
    fakes.report_error = OSError("disk full")
    run_one("w1")
    reload(db_session, ok, bad, report)
    assert (ok.status, bad.status, fakes.reports) == ("done", "dead", [report.id])
    assert (report.status, report.error) == ("failed", "OSError: disk full")


def test_main_once_runs_an_iteration_and_buries_a_hopeless_job(db_session, factory, fakes):
    job = factory.job(payload={"scan_id": 424242})
    assert main(["--once", "--worker-id", "w-test"]) == 0
    reload(db_session, job)
    assert (job.status, job.attempts, job.locked_by) == ("dead", 1, "w-test")
    assert (job.last_error, fakes.dispatched) == ("scan 424242 not found", 1)

"""End to end through the real components, no stand-ins: request_scan -> worker
run_one -> plugins on fixtures/ -> ingest -> outbox -> signed delivery, plus a
report job and a replayed dead job. Real commits on several connections."""

import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import cli
from app.api.main import create_app
from app.core.config import override_settings
from app.core.db import session_scope
from app.models import Finding, FindingEvent, Job, OutboxEntry, Report, Scan
from app.reporting.service import request_report
from app.reporting.webhooks import dispatch_pending, verify
from app.schemas.finding import FindingFilters
from app.worker.queue import claim
from app.worker.reaper import reap_stale
from app.worker.runner import run_one
from app.worker.scans import request_scan
from tests.factories import Factory

pytestmark = pytest.mark.committed

HOST = "tracking.acme-shipping.example.test"
PLUGINS = ["http_headers", "tls_config", "dependency_audit"]
SECRET = "DEMO-whsec-integration"


@pytest.fixture
def world(committed_session, frozen_clock, caplog):
    caplog.set_level(logging.INFO)  # production level: log calls must not raise
    make = Factory(committed_session)
    tenant = make.tenant("acme-shipping", timezone="Europe/Amsterdam", allowed_ports=[80, 443])
    asset = make.asset(tenant, hostname=HOST, tags=["prod"])
    profile = make.profile(tenant, name="baseline", plugins=PLUGINS)
    hook = make.webhook(tenant, secret=SECRET)
    committed_session.commit()
    return tenant, asset, profile, hook


def scan_now(tenant, asset, profile) -> int:
    with session_scope() as session:
        return request_scan(session, tenant_id=tenant.id, profile_id=profile.id,
                            asset_id=asset.id, trigger="api").id


def findings(asset_id: int) -> dict[tuple[str, str], str]:
    with session_scope() as session:
        rows = session.scalars(select(Finding).where(Finding.asset_id == asset_id))
        return {(f.plugin, f.rule_id): f.status for f in rows}


def pending_events() -> list[str]:
    with session_scope() as session:
        query = select(OutboxEntry.event).where(OutboxEntry.status == "pending")
        return sorted(session.scalars(query.order_by(OutboxEntry.id)))


def test_scan_ingest_webhooks_and_lifecycle(world, frozen_clock, caplog):
    tenant, asset, profile, hook = world
    scan_id = scan_now(tenant, asset, profile)
    assert run_one("it-worker") is not None
    with session_scope() as session:
        scan = session.get_one(Scan, scan_id)
        assert (scan.status, scan.error, scan.started_at) == ("completed", None, frozen_clock.now())
    first = findings(asset.id)
    assert first == {
        ("http_headers", "missing-csp"): "new", ("http_headers", "version-disclosure"): "new",
        ("tls_config", "cert-expiring-soon"): "new",
        ("dependency_audit", "TWSA-2025-0141"): "new",
        ("dependency_audit", "TWSA-2025-0187"): "new",
        ("dependency_audit", "TWSA-2026-0017"): "new",
    }
    assert pending_events() == ["finding.new"] * 6 + ["scan.completed"]
    assert {"ingest.completed", "scan.finished"} <= {r.msg for r in caplog.records}

    received: list[httpx.Request] = []

    def receiver(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(204)

    with session_scope() as session, httpx.Client(transport=httpx.MockTransport(receiver)) as c:
        assert dispatch_pending(session, client=c) == 7
    now = int(frozen_clock.now().timestamp())
    for request in received:
        assert request.url == hook.url
        assert verify(SECRET, request.headers["X-Tidewright-Signature"], request.content, now=now)
    body = json.loads(received[-1].content)
    assert (body["event"], body["data"]["scan_id"]) == ("scan.completed", scan_id)
    assert pending_events() == []

    # The team stops caring about CSP: narrowing the plugin's scope closes the finding.
    with session_scope() as session:
        session.merge(profile).options = {"plugins": {"http_headers": {"ignore_rules":
                                                                       ["missing-csp"]}}}
    frozen_clock.advance(hours=1)
    scan_now(tenant, asset, profile)
    run_one("it-worker")
    second = findings(asset.id)
    assert second.pop(("http_headers", "missing-csp")) == "fixed"
    assert set(second.values()) == {"open"}
    assert pending_events() == ["finding.fixed", "scan.completed"]


def test_report_job_writes_the_file(world, tmp_path):
    tenant, asset, profile, _ = world
    with override_settings(reports_dir=tmp_path):
        scan_now(tenant, asset, profile)
        run_one("it-worker")
        with session_scope() as session:
            filters = FindingFilters(severity=["high", "critical"])
            report_id = request_report(session, tenant.id, "json", filters).id
        run_one("it-worker")
        with session_scope() as session:
            report = session.get_one(Report, report_id)
            assert (report.status, report.error) == ("ready", None)
            doc = json.loads((tmp_path / str(tenant.id) / f"report-{report_id}.json").read_text())
    assert report.path.endswith(f"report-{report_id}.json")
    assert doc["count"] == 1 and doc["findings"][0]["rule_id"] == "TWSA-2025-0141"


def test_dead_job_fails_queued_scan_and_replay_runs_it(world, frozen_clock, capsys):
    tenant, asset, profile, _ = world
    scan_id = scan_now(tenant, asset, profile)
    with session_scope() as session:  # a worker claims the job, then is OOM-killed
        job = claim(session, "doomed-worker")
        job.max_attempts = 1
    frozen_clock.advance(minutes=5)
    with session_scope() as session:
        assert reap_stale(session) == [job.id]
    with session_scope() as session:
        assert session.get_one(Job, job.id).status == "dead"
        scan = session.get_one(Scan, scan_id)
        assert (scan.status, scan.started_at) == ("failed", None)  # died before it started

    assert cli.main(["replay-job", str(job.id), "--yes"]) == 0
    assert "back to queued" in capsys.readouterr().out
    assert run_one("it-worker") == job.id
    with session_scope() as session:
        assert session.get_one(Scan, scan_id).status == "completed"
        assert session.get_one(Job, job.id).status == "done"
        assert session.scalar(select(func.count()).select_from(Finding)) == 6
    assert len(pending_events()) == 6 + 2  # scan.failed, then the replay's results


def create_suppression_via_api(tenant):
    with session_scope() as session:
        _, token = Factory(session).token(tenant)
    with TestClient(create_app()) as client:
        response = client.post("/v1/suppression-rules", headers={
            "Authorization": f"Bearer {token}",
        }, json={"plugin": "http_headers", "rule_id": "missing-csp", "reason": "test staging"})
    assert response.status_code == 201, response.text
    return response.json()


def test_suppression_api_through_worker_and_real_outbox(world):
    tenant, asset, profile, _ = world
    rule = create_suppression_via_api(tenant)
    scan_now(tenant, asset, profile)
    run_one("suppression-worker")
    with session_scope() as session:
        finding = session.scalars(select(Finding).where(
            Finding.asset_id == asset.id, Finding.rule_id == "missing-csp",
        )).one()
        assert finding.triage == "accepted_risk"
        audit = session.scalars(select(FindingEvent).where(
            FindingEvent.finding_id == finding.id, FindingEvent.kind == "triage_changed",
        )).one()
        assert audit.data["suppression_rule_id"] == rule["id"]
        outbox = list(session.scalars(select(OutboxEntry)))
        assert len(outbox) == 6  # five unsuppressed findings + scan.completed
        assert not any(o.payload.get("finding_id") == finding.id for o in outbox)


def test_suppression_rolls_back_with_failed_job(world, monkeypatch):
    tenant, asset, profile, _ = world
    create_suppression_via_api(tenant)

    def fail_after_ingest(*args, **kwargs):
        raise RuntimeError("test: failure after findings, audit and outbox")

    monkeypatch.setattr("app.worker.runner.notify", fail_after_ingest)
    scan_now(tenant, asset, profile)
    run_one("suppression-worker")
    with session_scope() as session:
        for model in (Finding, FindingEvent, OutboxEntry):
            assert session.scalar(select(func.count()).select_from(model)) == 0
        assert session.scalar(select(Job.status)) == "failed"

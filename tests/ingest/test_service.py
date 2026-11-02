from __future__ import annotations

import dataclasses

import pytest
from sqlalchemy import select

from app.core.contracts import PluginContext
from app.core.errors import Conflict, ValidationFailed
from app.ingest.fingerprint import compute_fingerprint
from app.ingest.service import ingest_scan_results
from app.models import Finding, FindingEvent
from app.plugins.registry import get_plugin
from tests.plugins.conftest import FIXTURES, snapshot


def findings(world) -> list[Finding]:
    return list(world.session.scalars(select(Finding).order_by(Finding.id)))


def events(world, finding) -> list[tuple[str, str]]:
    stmt = select(FindingEvent).where(FindingEvent.finding_id == finding.id)
    stmt = stmt.order_by(FindingEvent.id)
    return [(e.kind, e.actor) for e in world.session.scalars(stmt)]


def test_first_scan_creates_new_findings(world, outbox):
    cert_result = world.raw("cert-expiring-soon", "tls:443", plugin="tls_config")
    summary = world.scan([world.raw(), cert_result])
    hsts, cert = findings(world)
    assert summary.counts() == {"created": 2, "seen": 0, "reopened": 0, "fixed": 0, "duplicates": 0}
    assert (hsts.status, hsts.severity, cert.severity) == ("new", "low", "medium")
    location = "header:strict-transport-security"
    assert hsts.fingerprint == compute_fingerprint(
        world.asset.tenant_id, world.asset.id, "http_headers", "missing-hsts", location
    )
    assert hsts.scan_id_last == world.scans[0].id and hsts.first_seen_at == hsts.last_seen_at
    assert events(world, hsts) == [("created", "system:ingest")]
    sent = [(e, p["finding_id"]) for e, p in outbox]
    assert sent == [("finding.new", hsts.id), ("finding.new", cert.id)]


def test_seen_again_becomes_open_without_notification(world, outbox, frozen_clock):
    world.scan([world.raw()])
    first_seen = findings(world)[0].first_seen_at
    frozen_clock.advance(hours=24)
    summary = world.scan([world.raw()])
    [finding] = findings(world)
    assert summary.seen == [finding.id] and finding.status == "open"
    assert finding.first_seen_at == first_seen and finding.last_seen_at == frozen_clock.now()
    assert finding.scan_id_last == world.scans[1].id
    assert [e for e, _ in outbox] == ["finding.new"]


def test_absent_from_completed_plugin_is_fixed_then_reopened(world, outbox, frozen_clock):
    world.scan([world.raw()])
    frozen_clock.advance(days=1)
    assert len(world.scan([]).fixed) == 1
    [finding] = findings(world)
    assert (finding.status, finding.fixed_at) == ("fixed", frozen_clock.now())
    assert world.scan([]).counts()["fixed"] == 0  # already fixed: unchanged
    frozen_clock.advance(days=1)
    assert world.scan([world.raw()]).reopened == [finding.id]
    assert (finding.status, finding.fixed_at) == ("reopened", None)
    assert world.scan([world.raw()]).seen == [finding.id] and finding.status == "open"
    assert [k for k, _ in events(world, finding)] == ["created", "fixed", "reopened"]
    assert [e for e, _ in outbox] == ["finding.new", "finding.fixed", "finding.reopened"]


def test_plugin_that_did_not_complete_never_fixes(world, outbox):
    expired = world.raw("cert-expired", "tls:443", plugin="tls_config")
    world.scan([world.raw(), expired])
    summary = world.scan([expired], completed=["tls_config"])
    assert summary.fixed == [] and {f.status for f in findings(world)} == {"new", "open"}


def test_other_assets_are_untouched(world):
    other = world.factory.asset(world.tenant)
    stranger = world.factory.finding(asset=other, plugin="http_headers", status="open")
    world.scan([])
    assert stranger.status == "open"


def test_triage_is_preserved_and_false_positives_stay_silent(world, outbox):
    world.scan([world.raw(), world.raw("missing-csp", "header:content-security-policy")])
    accepted, silenced = findings(world)
    accepted.triage, silenced.triage = "accepted_risk", "false_positive"
    outbox.clear()
    world.scan([])
    world.scan([world.raw(), world.raw("missing-csp", "header:content-security-policy")])
    assert (accepted.triage, silenced.triage) == ("accepted_risk", "false_positive")
    assert (accepted.status, silenced.status) == ("reopened", "reopened")
    assert [(e, p["finding_id"]) for e, p in outbox] == [
        ("finding.fixed", accepted.id), ("finding.reopened", accepted.id)
    ]
    assert [k for k, _ in events(world, silenced)] == ["created", "fixed", "reopened"]


def test_batch_duplicates_and_severity_changes(world):
    first = world.scan([world.raw(), world.raw(location="header:Strict-Transport-Security")])
    assert (len(first.created), first.duplicates) == (1, 1)
    summary = world.scan([world.raw(severity="high", title="HSTS missing (retitled)")])
    [finding] = findings(world)
    assert summary.counts()["seen"] == 1
    assert (finding.severity, finding.title) == ("high", "HSTS missing (retitled)")
    assert [k for k, _ in events(world, finding)] == ["created", "severity_changed"]


def test_location_separates_findings_since_v1_7(world):
    secrets = ["file:config/app.env:3", "file:config/worker.env:3"]
    results = [
        world.raw("generic-api-key", loc, plugin="secrets_in_config", severity="high")
        for loc in secrets
    ]
    world.scan(results, completed=["secrets_in_config"])
    summary = world.scan(results[:1], completed=["secrets_in_config"])
    assert [f.status for f in findings(world)] == ["open", "fixed"] and len(summary.fixed) == 1


def test_inconsistent_input_is_rejected(world):
    with pytest.raises(ValidationFailed, match="not in completed_plugins"):
        world.scan([world.raw()], completed=["tls_config"])
    scan = world.factory.scan(asset=world.asset, profile=world.profile, status="cancelled")
    with pytest.raises(Conflict):
        ingest_scan_results(world.session, scan, [], completed_plugins=["http_headers"])


def test_real_plugin_results_end_to_end(world, outbox):
    asset = dataclasses.replace(snapshot(world.asset.hostname), id=world.asset.id)
    results = get_plugin("dependency_audit").run(asset, {}, PluginContext(fixtures_root=FIXTURES))
    world.scan(results, completed=["dependency_audit"])
    assert {(f.rule_id, f.severity) for f in findings(world)} == {
        ("TWSA-2025-0141", "high"), ("TWSA-2025-0187", "medium"), ("TWSA-2026-0017", "medium"),
    }

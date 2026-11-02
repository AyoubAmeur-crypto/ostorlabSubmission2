from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.ingest.suppression import active_rules, matcher_key
from app.models import FindingEvent, SuppressionRule
from tests.ingest.test_suppression import add_rule, by_rule


def test_other_tenant_cannot_suppress(world):
    tenant = world.factory.tenant()
    world.session.add(SuppressionRule(
        tenant_id=tenant.id, plugin="http_headers", rule_id="missing-hsts",
        reason="foreign rule", matcher_key=matcher_key("http_headers", "missing-hsts"),
        created_by="token:foreign",
    ))
    world.session.flush()
    world.scan([world.raw()])
    assert by_rule(world)["missing-hsts"].triage == "none"


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_expiry_boundary(world, frozen_clock, offset):
    add_rule(world, expires_at=frozen_clock.now() + timedelta(seconds=offset))
    world.scan([world.raw()])
    expected = "accepted_risk" if offset > 0 else "none"
    assert by_rule(world)["missing-hsts"].triage == expected


def test_suppression_has_auditable_provenance(world, frozen_clock):
    rule = add_rule(world)
    world.scan([world.raw()])
    finding = by_rule(world)["missing-hsts"]
    events = list(world.session.scalars(select(FindingEvent).where(
        FindingEvent.finding_id == finding.id, FindingEvent.kind == "triage_changed",
    )))
    assert len(events) == 1
    assert (events[0].at, events[0].actor) == (frozen_clock.now(), "system:ingest")
    assert events[0].data == {
        "from": "none", "to": "accepted_risk", "suppression_rule_id": rule.id,
        "reason": rule.reason,
    }


@pytest.mark.parametrize("reopen", [False, True])
def test_suppression_prevents_new_and_reopened_webhooks(world, outbox, reopen):
    if reopen:
        world.scan([world.raw()])
        world.scan([])
    add_rule(world)
    outbox.clear()
    world.scan([world.raw(), world.raw("missing-csp", "header:content-security-policy")])
    finding = by_rule(world)["missing-hsts"]
    assert finding.triage == "accepted_risk"
    assert [payload["rule_id"] for _, payload in outbox] == ["missing-csp"]


@pytest.mark.parametrize("triage", ["accepted_risk", "false_positive"])
def test_human_triage_survives_reopen(world, triage):
    world.scan([world.raw()])
    finding = by_rule(world)["missing-hsts"]
    finding.triage = triage
    world.session.flush()
    world.scan([])
    add_rule(world)
    world.scan([world.raw()])
    assert finding.status == "reopened"
    assert finding.triage == triage
    assert not list(world.session.scalars(select(FindingEvent).where(
        FindingEvent.finding_id == finding.id, FindingEvent.kind == "triage_changed",
    )))


def test_candidate_query_does_not_fetch_other_tenants_or_expired_rules(world, frozen_clock):
    own = add_rule(world)
    add_rule(world, expires_at=frozen_clock.now())
    other = world.factory.tenant()
    world.session.add(SuppressionRule(
        tenant_id=other.id, plugin=own.plugin, rule_id=own.rule_id, matcher_key=own.matcher_key,
        reason="other", created_by="token:other",
    ))
    world.scan([world.raw()])
    finding = by_rule(world)["missing-hsts"]
    assert [r.id for r in active_rules(world.session, [finding])] == [own.id]


def test_automatic_suppression_still_silences_later_reopen(world, outbox):
    add_rule(world)
    world.scan([world.raw()])
    world.scan([])
    outbox.clear()
    world.scan([world.raw()])
    assert by_rule(world)["missing-hsts"].triage == "accepted_risk"
    assert outbox == []


def test_first_matching_rule_wins_and_only_one_audit_event(world):
    first = add_rule(world)
    add_rule(world)
    world.scan([world.raw()])
    events = list(world.session.scalars(select(FindingEvent).where(
        FindingEvent.kind == "triage_changed",
    )))
    assert len(events) == 1
    assert events[0].data["suppression_rule_id"] == first.id


def test_ingest_refreshes_triage_before_reopening_a_cached_finding(world):
    world.scan([world.raw()])
    finding = by_rule(world)["missing-hsts"]
    world.scan([])
    # Simulate a human decision made outside this session's identity map.
    world.session.execute(update(type(finding)).where(type(finding).id == finding.id)
                          .values(triage="false_positive"),
                          execution_options={"synchronize_session": False})
    assert finding.triage == "none"  # stale cached object
    add_rule(world)
    world.scan([world.raw()])
    assert finding.triage == "false_positive"

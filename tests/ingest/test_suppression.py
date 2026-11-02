from datetime import timedelta

from sqlalchemy import select

from app.ingest.suppression import WILDCARD, matcher_key
from app.models import Finding, SuppressionRule


def add_rule(world, **fields):
    fields.setdefault("plugin", "http_headers")
    fields.setdefault("rule_id", "missing-hsts")
    rule = SuppressionRule(
        tenant_id=world.tenant.id,
        reason=fields.pop("reason", "accepted by the customer"),
        matcher_key=matcher_key(fields["plugin"], fields.get("rule_id")),
        created_by="token:tests",
        **fields,
    )
    world.session.add(rule)
    world.session.flush()
    return rule


def by_rule(world) -> dict[str, Finding]:
    rows = world.session.scalars(select(Finding).order_by(Finding.id))
    return {f.rule_id: f for f in rows}


def test_matcher_key():
    assert matcher_key("http_headers", "missing-hsts") == "http_headers\x1fmissing-hsts"
    assert matcher_key("http_headers", None) == f"http_headers\x1f{WILDCARD}"


def test_new_matching_finding_is_accepted_risk(world):
    add_rule(world)
    world.scan([world.raw(), world.raw("missing-csp", "header:content-security-policy")])
    found = by_rule(world)
    assert found["missing-hsts"].triage == "accepted_risk"
    assert found["missing-csp"].triage == "none"


def test_legacy_rule_without_rule_id_does_not_suppress(world):
    add_rule(world, rule_id=None)
    world.scan([world.raw(), world.raw("missing-csp", "header:content-security-policy")])
    assert {f.triage for f in by_rule(world).values()} == {"none"}


def test_location_prefix_narrows_the_rule(world):
    add_rule(world, plugin="tls_config", rule_id="cert-expired", location_prefix="tls:8443")
    world.scan(
        [
            world.raw("cert-expired", "tls:443", plugin="tls_config"),
            world.raw("cert-expired", "tls:8443", plugin="tls_config"),
        ]
    )
    rows = world.session.scalars(select(Finding).order_by(Finding.location))
    assert [(f.location, f.triage) for f in rows] == [
        ("tls:443", "none"),
        ("tls:8443", "accepted_risk"),
    ]


def test_asset_id_narrows_the_rule(world):
    other = world.factory.asset(world.tenant)
    add_rule(world, asset_id=other.id)
    world.scan([world.raw()])
    assert by_rule(world)["missing-hsts"].triage == "none"


def test_reopened_finding_is_suppressed(world, frozen_clock):
    world.scan([world.raw()])
    frozen_clock.advance(days=1)
    world.scan([])
    add_rule(world)
    frozen_clock.advance(days=1)
    summary = world.scan([world.raw()])
    finding = by_rule(world)["missing-hsts"]
    assert summary.reopened == [finding.id]
    assert finding.triage == "accepted_risk"


def test_seen_again_is_not_retriaged(world, frozen_clock):
    world.scan([world.raw()])
    add_rule(world)
    frozen_clock.advance(days=1)
    world.scan([world.raw()])
    assert by_rule(world)["missing-hsts"].triage == "none"


def test_expired_rule_does_not_suppress(world, frozen_clock):
    add_rule(world, expires_at=frozen_clock.now() + timedelta(hours=1))
    frozen_clock.advance(hours=2)
    world.scan([world.raw()])
    assert by_rule(world)["missing-hsts"].triage == "none"

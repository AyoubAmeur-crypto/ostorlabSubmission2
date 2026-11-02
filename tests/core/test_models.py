from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.db import session_scope
from app.models import Asset, Finding, FindingEvent, Scan, Tenant
from app.models.enums import FindingStatus, ScanStatus, Severity


def test_timestamps_come_from_the_app_clock(factory, frozen_clock):
    tenant = factory.tenant()
    asset = factory.asset(tenant)
    assert tenant.created_at == frozen_clock.now()
    assert asset.created_at.tzinfo is not None


def test_round_trip_keeps_utc_awareness(factory, db_session, frozen_clock):
    scan = factory.scan(started_at=frozen_clock.now() + timedelta(minutes=1))
    db_session.expire_all()
    loaded = db_session.get(Scan, scan.id)
    assert loaded.started_at == frozen_clock.now() + timedelta(minutes=1)
    assert loaded.started_at.utcoffset() == timedelta(0)
    assert loaded.status == ScanStatus.QUEUED


def test_server_defaults_cover_raw_inserts(db_session):
    db_session.execute(text("INSERT INTO tenants (slug, name) VALUES ('raw-co', 'Raw Co')"))
    tenant = db_session.scalars(select(Tenant).where(Tenant.slug == "raw-co")).one()
    assert tenant.timezone == "UTC"
    assert tenant.allowed_ports == []
    assert tenant.created_at is not None


def test_tenant_slug_is_unique(factory, db_session):
    factory.tenant(slug="lumen-retail")
    with pytest.raises(IntegrityError):
        factory.tenant(slug="lumen-retail")


def test_active_hostname_is_unique_per_tenant_but_archived_rows_are_kept(
    factory, db_session, frozen_clock
):
    acme = factory.tenant(slug="acme-shipping")
    north = factory.tenant(slug="northpeak-health")
    old = factory.asset(acme, hostname="portal.acme-shipping.example.test")
    # Same hostname in another tenant is fine.
    factory.asset(north, hostname="portal.acme-shipping.example.test")
    old.archived_at = frozen_clock.now()
    db_session.flush()
    # Re-registering after archive is fine too.
    factory.asset(acme, hostname="portal.acme-shipping.example.test")
    with pytest.raises(IntegrityError):
        factory.asset(acme, hostname="portal.acme-shipping.example.test")


def test_finding_fingerprint_unique_per_tenant(factory, db_session):
    a1 = factory.asset(factory.tenant())
    a2 = factory.asset(factory.tenant())
    factory.finding(asset=a1, fingerprint="f" * 64)
    factory.finding(asset=a2, fingerprint="f" * 64)
    with pytest.raises(IntegrityError):
        factory.finding(asset=a1, fingerprint="f" * 64)


@pytest.mark.parametrize(
    ("column", "value"),
    [("severity", "urgent"), ("status", "closed"), ("triage", "ignored")],
)
def test_check_constraints_reject_unknown_values(factory, db_session, column, value):
    finding = factory.finding()
    setattr(finding, column, value)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_scheduled_slot_is_unique_per_profile_and_asset(factory, db_session, frozen_clock):
    asset = factory.asset()
    profile = factory.profile(db_session.get(Tenant, asset.tenant_id))
    slot = frozen_clock.now()
    factory.scan(asset=asset, profile=profile, trigger="schedule", scheduled_slot=slot)
    # Manual scans have no slot and never collide.
    factory.scan(asset=asset, profile=profile)
    factory.scan(asset=asset, profile=profile)
    with pytest.raises(IntegrityError):
        factory.scan(asset=asset, profile=profile, trigger="schedule", scheduled_slot=slot)


def test_finding_events_cascade_with_finding(factory, db_session):
    finding = factory.finding(severity=Severity.HIGH)
    factory.finding_event(finding)
    factory.finding_event(finding, "triage_changed", actor="token:tw_demo_ab12")
    assert [e.kind for e in finding.events] == ["created", "triage_changed"]
    db_session.delete(finding)
    db_session.flush()
    assert db_session.scalars(select(FindingEvent)).all() == []


def test_deleting_a_scan_keeps_findings(factory, db_session):
    scan = factory.scan(status="completed")
    finding = factory.finding(scan=scan)
    db_session.delete(scan)
    db_session.flush()
    db_session.refresh(finding)
    assert finding.scan_id_last is None


def test_session_scope_shares_the_test_transaction(factory, db_session):
    tenant = factory.tenant(slug="northpeak-health")
    with session_scope() as other:
        asset = Asset(tenant_id=tenant.id, hostname="api.northpeak-health.example.test", kind="api")
        other.add(asset)
    assert db_session.scalars(select(Asset).where(Asset.tenant_id == tenant.id)).one().kind == "api"


def test_session_scope_rolls_back_on_error(factory, db_session):
    tenant = factory.tenant()
    with pytest.raises(RuntimeError), session_scope() as other:
        other.add(Asset(tenant_id=tenant.id, hostname="gone.example.test", kind="web"))
        other.flush()
        raise RuntimeError("boom")
    assert db_session.scalars(select(Asset).where(Asset.tenant_id == tenant.id)).all() == []


def test_enum_values_compare_as_strings(factory):
    finding = factory.finding(status=FindingStatus.REOPENED)
    assert finding.status == "reopened"
    assert Severity("critical").rank > Severity.HIGH.rank
    assert isinstance(finding, Finding)


@pytest.mark.committed
def test_committed_db_sees_data_from_other_connections(committed_db, committed_session):
    from tests.factories import Factory

    tenant = Factory(committed_session).tenant(slug="lumen-retail")
    committed_session.commit()
    with committed_db.connect() as conn:
        slug = conn.execute(text("SELECT slug FROM tenants WHERE id = :id"), {"id": tenant.id})
        assert slug.scalar_one() == "lumen-retail"

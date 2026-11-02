"""TODAY is 2026-10-14T09:00Z; the default lookback window is (08:55Z, 09:00Z]."""

from datetime import timedelta

from sqlalchemy import func, select

from app.models import Job, Scan
from app.scheduler import service
from tests.factories import TODAY


def count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def test_plans_one_scan_and_job_per_asset_and_slot(db_session, factory):
    tenant = factory.tenant(timezone="UTC")
    factory.profile(tenant, schedule_cron="*/5 * * * *")
    assets = [factory.asset(tenant), factory.asset(tenant)]
    scans = service.tick(db_session, TODAY)
    assert {(s.asset_id, s.scheduled_slot) for s in scans} == {(a.id, TODAY) for a in assets}
    assert {s.trigger for s in scans} == {"schedule"}
    assert count(db_session, Job) == 2


def test_overlapping_ticks_plan_each_slot_once(db_session, factory):
    tenant = factory.tenant(timezone="UTC")
    factory.profile(tenant, schedule_cron="* * * * *")
    factory.asset(tenant)
    assert len(service.tick(db_session, TODAY)) == 5
    assert len(service.tick(db_session, TODAY + timedelta(minutes=2))) == 2
    assert count(db_session, Scan) == 7


def test_unique_index_catches_a_slot_planned_concurrently(db_session, factory):
    tenant = factory.tenant(timezone="UTC")
    profile, asset = factory.profile(tenant, schedule_cron="0 9 * * *"), factory.asset(tenant)
    factory.scan(asset=asset, profile=profile, trigger="schedule", scheduled_slot=TODAY)
    assert service._plan(db_session, profile, asset, TODAY) is None
    assert count(db_session, Scan) == 1


def test_skips_disabled_unscheduled_invalid_archived_untagged(db_session, factory):
    tenant = factory.tenant(timezone="UTC")
    factory.profile(tenant, schedule_cron="0 9 * * *", enabled=False)
    factory.profile(tenant, schedule_cron=None)
    tagged = factory.profile(tenant, schedule_cron="0 9 * * *", options={"asset_tags": ["prod"]})
    prod = factory.asset(tenant, tags=["prod", "pci"])
    factory.asset(tenant, tags=["staging"])
    factory.asset(tenant, tags=["prod"], archived_at=TODAY - timedelta(days=1))
    factory.profile(tenant, schedule_cron="61 * * * *")  # invalid: skipped, not fatal
    scans = service.tick(db_session, TODAY)
    assert [(s.profile_id, s.asset_id) for s in scans] == [(tagged.id, prod.id)]


def test_evaluates_each_tenant_in_its_zone(db_session, factory):
    # 09:00Z is 20:00 in Sydney (AEDT) and 11:00 in Amsterdam.
    lumen = factory.tenant("lumen-retail", timezone="Australia/Sydney")
    acme = factory.tenant("acme-shipping", timezone="Europe/Amsterdam")
    broken = factory.tenant(timezone="Nowhere/Special")  # falls back to TW_DEFAULT_TIMEZONE=UTC
    for tenant, cron in ((lumen, "0 20 * * *"), (acme, "0 20 * * *"), (broken, "0 9 * * *")):
        factory.asset(tenant)
        factory.profile(tenant, schedule_cron=cron)
    assert [s.tenant_id for s in service.tick(db_session, TODAY)] == [lumen.id, broken.id]


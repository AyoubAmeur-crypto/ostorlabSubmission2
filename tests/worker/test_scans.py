import pytest
from sqlalchemy import select

from app.core.errors import Conflict, InvalidTransition, NotFound
from app.models import Job, Scan, ScanStatus
from app.worker.scans import request_scan, transition
from tests.factories import TODAY


def test_request_scan_inserts_queued_scan_and_its_job(db_session, factory, frozen_clock):
    tenant = factory.tenant()
    profile, asset = factory.profile(tenant), factory.asset(tenant)
    scan = request_scan(
        db_session, tenant_id=tenant.id, profile_id=profile.id, asset_id=asset.id, trigger="api"
    )
    assert (scan.status, scan.trigger, scan.requested_at) == ("queued", "api", TODAY)
    job = db_session.scalars(select(Job)).one()
    assert (job.kind, job.payload) == ("scan.run", {"scan_id": scan.id})


def test_request_scan_validates_ownership_and_state(db_session, factory):
    tenant, other = factory.tenant(), factory.tenant()
    profile, asset = factory.profile(tenant), factory.asset(tenant)
    cases = [
        (NotFound, factory.profile(other).id, asset.id),
        (NotFound, profile.id, factory.asset(other).id),
        (Conflict, profile.id, factory.asset(tenant, archived_at=TODAY).id),
        (Conflict, factory.profile(tenant, enabled=False).id, asset.id),
    ]
    for error, profile_id, asset_id in cases:
        ids = {"tenant_id": tenant.id, "profile_id": profile_id, "asset_id": asset_id}
        with pytest.raises(error):
            request_scan(db_session, **ids, trigger="manual")
    assert db_session.scalars(select(Scan)).all() == []


@pytest.mark.parametrize(
    ("current", "target"),
    [("queued", "completed"), ("running", "queued"), ("running", "running"),
     ("completed", "cancelled"), ("cancelled", "running"), ("failed", "completed")],
)
def test_transition_rejects_moves_the_state_machine_forbids(current, target):
    with pytest.raises(InvalidTransition):
        transition(Scan(status=current), ScanStatus(target))

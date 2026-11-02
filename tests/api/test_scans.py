from datetime import timedelta

import pytest

from app.models import ScanStatus


def test_create_scan_goes_through_request_scan(client, acme, northpeak, factory):
    body = {
        "profile_id": factory.profile(acme.tenant).id,
        "asset_id": factory.asset(acme.tenant).id,
    }
    response = client.post("/v1/scans", headers=acme.headers, json=body)
    assert response.status_code == 201
    assert (response.json()["status"], response.json()["trigger"]) == ("queued", "api")
    foreign = {**body, "asset_id": factory.asset(northpeak.tenant).id}
    assert client.post("/v1/scans", headers=acme.headers, json=foreign).status_code == 404


def test_list_is_newest_first_filtered_and_paginated(client, acme, northpeak, factory, today):
    asset = factory.asset(acme.tenant)
    statuses = ["completed", "failed", "completed", "queued"]
    scans = [
        factory.scan(asset=asset, status=s, requested_at=today - timedelta(hours=4 - n))
        for n, s in enumerate(statuses)
    ]
    factory.scan(asset=factory.asset(northpeak.tenant))

    page = client.get("/v1/scans", headers=acme.headers, params={"limit": 2}).json()
    assert page["total"] == 4
    assert [s["id"] for s in page["items"]] == [scans[3].id, scans[2].id]

    params = {"status": ["completed", "failed"], "asset_id": asset.id}
    filtered = client.get("/v1/scans", headers=acme.headers, params=params).json()
    assert {s["id"] for s in filtered["items"]} == {scans[0].id, scans[1].id, scans[2].id}
    bad = client.get("/v1/scans", headers=acme.headers, params={"status": "paused"})
    assert bad.status_code == 422


@pytest.mark.parametrize("status", [ScanStatus.QUEUED, ScanStatus.RUNNING])
def test_cancel_active_scan(client, acme, factory, frozen_clock, status):
    scan = factory.scan(asset=factory.asset(acme.tenant), status=status)
    response = client.post(f"/v1/scans/{scan.id}/cancel", headers=acme.headers)
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["finished_at"] == "2026-10-14T09:00:00Z"


@pytest.mark.parametrize("status", [ScanStatus.COMPLETED, ScanStatus.FAILED, ScanStatus.CANCELLED])
def test_cancel_finished_scan_is_invalid_transition(client, acme, factory, status):
    scan = factory.scan(asset=factory.asset(acme.tenant), status=status)
    response = client.post(f"/v1/scans/{scan.id}/cancel", headers=acme.headers)
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "invalid_transition"
    assert error["details"] == {"entity": "scan", "current": status.value, "target": "cancelled"}


def test_other_tenants_scan_is_404(client, acme, northpeak, factory):
    scan = factory.scan(asset=factory.asset(northpeak.tenant))
    assert client.get(f"/v1/scans/{scan.id}", headers=acme.headers).status_code == 404
    assert client.post(f"/v1/scans/{scan.id}/cancel", headers=acme.headers).status_code == 404
    assert client.get(f"/v1/scans/{scan.id}", headers=northpeak.headers).status_code == 200

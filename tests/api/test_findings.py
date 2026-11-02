from datetime import timedelta

import pytest

from app.models import Finding


@pytest.fixture
def findings(acme, northpeak, factory, today):
    web, api = factory.asset(acme.tenant), factory.asset(acme.tenant)

    def make(asset, days_ago, **fields):
        return factory.finding(asset=asset, last_seen_at=today - timedelta(days=days_ago), **fields)

    factory.finding(asset=factory.asset(northpeak.tenant), severity="critical")
    return {
        "old_low": make(web, 9, severity="low"),
        "high": make(web, 1, severity="high", status="open"),
        "critical": make(
            api, 0, plugin="dependency_audit", severity="critical", triage="accepted_risk"
        ),
        "fixed": make(api, 3, severity="medium", status="fixed"),
    }


def ids(client, caller, **params):
    response = client.get("/v1/findings", headers=caller.headers, params=params)
    assert response.status_code == 200, response.text
    return [f["id"] for f in response.json()["items"]]


def test_list_is_tenant_scoped_and_newest_first(client, acme, findings):
    assert ids(client, acme) == [findings[k].id for k in ("critical", "high", "fixed", "old_low")]


def test_filters_combine(client, acme, findings):
    critical, high, fixed = findings["critical"].id, findings["high"].id, findings["fixed"].id
    assert ids(client, acme, severity=["high", "critical"]) == [critical, high]
    assert ids(client, acme, status="fixed") == [fixed]
    assert ids(client, acme, triage="accepted_risk", plugin="dependency_audit") == [critical]
    assert ids(client, acme, asset_id=findings["fixed"].asset_id, severity="medium") == [fixed]
    assert ids(client, acme, since="2026-10-12T00:00:00+02:00") == [critical, high]


@pytest.mark.parametrize("params", [{"since": "2026-10-12T00:00:00"}, {"severity": "urgent"}])
def test_invalid_filters_are_422(client, acme, params):
    response = client.get("/v1/findings", headers=acme.headers, params=params)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_pagination_reports_the_filtered_total(client, acme, findings):
    params = {"limit": 1, "offset": 1, "status": ["new", "open"]}
    page = client.get("/v1/findings", headers=acme.headers, params=params).json()
    assert (page["total"], [f["id"] for f in page["items"]]) == (3, [findings["high"].id])


def test_triage_writes_one_audit_event(client, acme, findings, db_session):
    url = f"/v1/findings/{findings['high'].id}"
    body = {"triage": "false_positive", "note": "staging banner, not prod"}
    response = client.patch(url, headers=acme.headers, json=body)
    assert (response.json()["triage"], response.json()["status"]) == ("false_positive", "open")
    assert client.patch(url, headers=acme.headers, json=body).status_code == 200  # no-op

    events = client.get(f"{url}/events", headers=acme.headers).json()
    assert events["total"] == 1
    event = events["items"][0]
    assert (event["kind"], event["actor"]) == ("triage_changed", f"token:{acme.token[:12]}")
    assert event["data"] == {"from": "none", "to": "false_positive", "note": body["note"]}
    db_session.expire_all()
    assert db_session.get(Finding, findings["high"].id).triage == "false_positive"


def test_other_tenants_findings_are_404(client, northpeak, findings):
    url = f"/v1/findings/{findings['high'].id}"
    assert client.get(url, headers=northpeak.headers).status_code == 404
    assert client.get(f"{url}/events", headers=northpeak.headers).status_code == 404
    patch = client.patch(url, headers=northpeak.headers, json={"triage": "accepted_risk"})
    assert patch.status_code == 404

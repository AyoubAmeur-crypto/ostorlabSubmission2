import pytest

from tests.api.test_suppression_rules import create, rule_body


def test_cannot_delete_another_tenants_rule(client, acme, northpeak):
    rule = create(client, northpeak).json()
    path = f"/v1/suppression-rules/{rule['id']}"
    assert client.delete(path, headers=acme.headers).status_code == 404
    assert client.get(path, headers=northpeak.headers).status_code == 200


@pytest.mark.parametrize("value", [None, "", "   "])
def test_rule_id_must_be_specific(client, acme, value):
    assert create(client, acme, rule_id=value).status_code == 422


def test_rule_id_cannot_be_omitted(client, acme):
    body = rule_body()
    del body["rule_id"]
    response = client.post("/v1/suppression-rules", headers=acme.headers, json=body)
    assert response.status_code == 422


def test_reason_cannot_be_whitespace(client, acme):
    assert create(client, acme, reason="   ").status_code == 422


def test_reason_accepts_documented_maximum(client, acme):
    response = create(client, acme, reason="a" * 2000)
    assert response.status_code == 201, response.text
    assert response.json()["reason"] == "a" * 2000


@pytest.mark.parametrize("triage", ["none", "accepted_risk", "false_positive"])
def test_creation_leaves_backlog_unchanged(client, acme, factory, db_session, triage):
    asset = factory.asset(acme.tenant)
    finding = factory.finding(asset=asset, rule_id="missing-hsts", status="open", triage=triage)
    assert create(client, acme).status_code == 201
    db_session.refresh(finding)
    assert finding.triage == triage

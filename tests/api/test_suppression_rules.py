from app.models import Finding


def rule_body(**fields):
    body = {
        "plugin": "http_headers",
        "rule_id": "missing-hsts",
        "reason": "staging hosts serve HSTS from the CDN",
    }
    body.update(fields)
    return body


def create(client, caller, **fields):
    return client.post("/v1/suppression-rules", headers=caller.headers, json=rule_body(**fields))


def test_create_returns_the_rule(client, acme):
    response = create(client, acme, location_prefix="header:")
    assert response.status_code == 201, response.text
    rule = response.json()
    assert rule["tenant_id"] == acme.tenant.id
    assert (rule["plugin"], rule["rule_id"], rule["location_prefix"]) == (
        "http_headers",
        "missing-hsts",
        "header:",
    )
    assert rule["active"] is True
    assert rule["created_by"].startswith("token:")


def test_list_and_get(client, acme):
    first = create(client, acme).json()
    second = create(client, acme, rule_id="missing-csp").json()
    page = client.get("/v1/suppression-rules", headers=acme.headers).json()
    assert [r["id"] for r in page["items"]] == [first["id"], second["id"]]
    assert page["total"] == 2
    response = client.get(f"/v1/suppression-rules/{second['id']}", headers=acme.headers)
    assert response.json()["rule_id"] == "missing-csp"


def test_list_is_tenant_scoped(client, acme, northpeak):
    create(client, northpeak)
    page = client.get("/v1/suppression-rules", headers=acme.headers).json()
    assert page["items"] == [] and page["total"] == 0


def test_get_other_tenant_rule_is_404(client, acme, northpeak):
    rule = create(client, northpeak).json()
    response = client.get(f"/v1/suppression-rules/{rule['id']}", headers=acme.headers)
    assert response.status_code == 404


def test_delete(client, acme):
    rule = create(client, acme).json()
    response = client.delete(f"/v1/suppression-rules/{rule['id']}", headers=acme.headers)
    assert response.status_code == 204
    response = client.get(f"/v1/suppression-rules/{rule['id']}", headers=acme.headers)
    assert response.status_code == 404


def test_delete_unknown_is_404(client, acme):
    response = client.delete("/v1/suppression-rules/999999", headers=acme.headers)
    assert response.status_code == 404


def test_reason_is_required(client, acme):
    body = rule_body()
    del body["reason"]
    response = client.post("/v1/suppression-rules", headers=acme.headers, json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_unknown_plugin_is_422(client, acme):
    response = create(client, acme, plugin="cookie_flags")
    assert response.status_code == 422


def test_asset_of_other_tenant_is_404(client, acme, northpeak, factory):
    asset = factory.asset(northpeak.tenant)
    response = create(client, acme, asset_id=asset.id)
    assert response.status_code == 404


def test_naive_expiry_is_422(client, acme):
    response = create(client, acme, expires_at="2026-12-31T00:00:00")
    assert response.status_code == 422


def test_create_leaves_existing_findings_unchanged(client, acme, factory, db_session):
    asset = factory.asset(acme.tenant)
    hit = factory.finding(asset=asset, rule_id="missing-hsts", status="open")
    miss = factory.finding(asset=asset, rule_id="missing-csp", status="open")
    gone = factory.finding(asset=asset, rule_id="missing-hsts", location="header:x", status="fixed")
    assert create(client, acme).status_code == 201
    for finding in (hit, miss, gone):
        db_session.refresh(finding)
    assert db_session.get(Finding, hit.id).triage == "none"
    assert (miss.triage, gone.triage) == ("none", "none")

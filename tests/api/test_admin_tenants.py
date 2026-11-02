def test_create_tenant_issue_token_and_use_it(client, admin_headers):
    body = {
        "slug": "lumen-retail",
        "name": "Lumen Retail",
        "timezone": "Australia/Sydney",
        "allowed_ports": [8443, 443, 80, 443],
    }
    created = client.post("/v1/admin/tenants", headers=admin_headers, json=body)
    assert created.status_code == 201
    tenant = created.json()
    assert tenant["allowed_ports"] == [80, 443, 8443]

    issued = client.post(
        f"/v1/admin/tenants/{tenant['id']}/tokens", headers=admin_headers, json={"label": "ci"}
    )
    assert issued.status_code == 201
    token = issued.json()["token"]
    assert token.startswith("tw_demo_") and issued.json()["prefix"] == token[:12]

    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/v1/assets", headers=headers).status_code == 200

    token_id = issued.json()["id"]
    revoke = client.delete(
        f"/v1/admin/tenants/{tenant['id']}/tokens/{token_id}", headers=admin_headers
    )
    assert revoke.status_code == 204
    assert client.get("/v1/assets", headers=headers).status_code == 401


def test_update_tenant_and_duplicate_slug(client, admin_headers, acme):
    url = f"/v1/admin/tenants/{acme.tenant.id}"
    response = client.patch(url, headers=admin_headers, json={"timezone": "Europe/Amsterdam"})
    assert response.json()["timezone"] == "Europe/Amsterdam"
    bad = client.patch(url, headers=admin_headers, json={"timezone": "Mars/Base"})
    assert bad.status_code == 422
    again = {"slug": "acme-shipping", "name": "Again"}
    assert client.post("/v1/admin/tenants", headers=admin_headers, json=again).status_code == 409


def test_token_of_another_tenant_cannot_be_revoked_through_this_one(
    client, admin_headers, acme, northpeak, factory
):
    row, _ = factory.token(northpeak.tenant)
    url = f"/v1/admin/tenants/{acme.tenant.id}/tokens/{row.id}"
    assert client.delete(url, headers=admin_headers).status_code == 404

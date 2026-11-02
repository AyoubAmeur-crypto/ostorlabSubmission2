from app.core import auth


def test_healthz_needs_no_token(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": True}
    assert response.headers["X-Request-ID"]


def test_missing_token_is_401_with_error_body(client):
    response = client.get("/v1/assets")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "unauthorized"


def test_unknown_and_revoked_tokens_are_rejected(client, acme, factory, db_session):
    forged = {"Authorization": "Bearer tw_demo_" + "x" * auth.TOKEN_BODY_LENGTH}
    assert client.get("/v1/assets", headers=forged).status_code == 401

    # Same prefix as a real token, different body: the hash check must fail.
    lookalike = (acme.token[: auth.PREFIX_LENGTH] + "0" * auth.TOKEN_BODY_LENGTH)[: len(acme.token)]
    assert (
        client.get("/v1/assets", headers={"Authorization": f"Bearer {lookalike}"}).status_code
        == 401
    )

    row, token = factory.token(acme.tenant, label="to-revoke")
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/v1/assets", headers=headers).status_code == 200
    auth.revoke_api_token(db_session, row)
    assert client.get("/v1/assets", headers=headers).status_code == 401


def test_tenant_token_is_not_an_admin_token(client, acme, admin_headers):
    assert client.get("/v1/admin/tenants", headers=acme.headers).status_code == 401
    assert client.get("/v1/admin/tenants", headers=admin_headers).status_code == 200


def test_errors_share_one_shape(client, acme):
    assert client.get("/v1/nope").json()["error"]["code"] == "not_found"
    body = {"hostname": "a.example.test", "colour": "blue"}
    response = client.post("/v1/assets", headers=acme.headers, json=body)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert error["details"]["errors"][0]["loc"] == ["body", "colour"]


def test_request_id_is_echoed_only_when_well_formed(client):
    echoed = client.get("/healthz", headers={"X-Request-ID": "req-42"})
    assert echoed.headers["X-Request-ID"] == "req-42"
    replaced = client.get("/healthz", headers={"X-Request-ID": "two words"})
    assert replaced.headers["X-Request-ID"] != "two words"

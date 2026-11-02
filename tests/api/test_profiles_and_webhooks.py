PROFILE = {
    "name": "nightly-web",
    "plugins": ["http_headers", "tls_config"],
    "options": {"timeout_s": 10, "plugins": {"tls_config": {"min_days": 14}}},
    "schedule_cron": "0  2 * * *",
}


def test_create_and_update_profile(client, acme, northpeak):
    created = client.post("/v1/profiles", headers=acme.headers, json=PROFILE)
    assert created.status_code == 201
    assert client.post("/v1/profiles", headers=acme.headers, json=PROFILE).status_code == 409
    assert client.post("/v1/profiles", headers=northpeak.headers, json=PROFILE).status_code == 201
    profile = created.json()
    assert profile["schedule_cron"] == "0 2 * * *"
    assert profile["options"]["plugins"] == {"tls_config": {"min_days": 14}}

    url = f"/v1/profiles/{profile['id']}"
    unschedule = {"schedule_cron": None, "enabled": False}
    patched = client.patch(url, headers=acme.headers, json=unschedule)
    assert (patched.json()["schedule_cron"], patched.json()["enabled"]) == (None, False)
    assert patched.json()["plugins"] == PROFILE["plugins"]


def test_profile_validation(client, acme):
    nmap = {**PROFILE, "plugins": ["nmap"]}
    unknown = client.post("/v1/profiles", headers=acme.headers, json=nmap)
    assert unknown.status_code == 422
    assert unknown.json()["error"]["details"] == {"plugins": ["nmap"]}
    stray = {**PROFILE, "plugins": ["http_headers"]}
    assert client.post("/v1/profiles", headers=acme.headers, json=stray).status_code == 422
    bad_cron = {**PROFILE, "schedule_cron": "0 2 * * mon?"}
    assert client.post("/v1/profiles", headers=acme.headers, json=bad_cron).status_code == 422


def test_webhook_secret_is_write_only(client, acme, northpeak):
    body = {
        "url": "http://mockhook:9000/hooks/acme-shipping",
        "secret": "DEMO-whsec-acme-000000",
        "events": ["scan.completed", "finding.new"],
    }
    created = client.post("/v1/webhooks", headers=acme.headers, json=body)
    assert created.status_code == 201
    webhook = created.json()
    assert "secret" not in webhook and webhook["events"] == ["finding.new", "scan.completed"]

    url = f"/v1/webhooks/{webhook['id']}"
    patched = client.patch(url, headers=acme.headers, json={"enabled": False})
    assert patched.json()["enabled"] is False and "secret" not in patched.json()
    assert client.get(url, headers=northpeak.headers).status_code == 404
    assert client.delete(url, headers=northpeak.headers).status_code == 404
    assert client.delete(url, headers=acme.headers).status_code == 204
    assert client.get(url, headers=acme.headers).status_code == 404

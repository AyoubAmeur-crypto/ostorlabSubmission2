from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.core.errors import PluginDataMissing, PluginError
from app.plugins.registry import get_plugin
from app.plugins.tls_config import hostname_matches
from tests.plugins.conftest import rules, snapshot


def run(name, hostname, ctx, options=None, **asset):
    return get_plugin(name).run(snapshot(hostname, **asset), options or {}, ctx)


def test_http_headers(ctx):
    assert run("http_headers", "www.acme-shipping.example.test", ctx) == []
    assert rules(run("http_headers", "tracking.acme-shipping.example.test", ctx)) == {
        ("missing-csp", "header:content-security-policy"),
        ("version-disclosure", "header:server"),
    }
    # APIs are not checked for CSP or framing; a one-day HSTS is too short.
    fhir = run("http_headers", "fhir-api.northpeak-health.example.test", ctx, kind="api")
    assert rules(fhir) == {("weak-hsts", "header:strict-transport-security")}
    options = {"ignore_rules": ["version-disclosure"]}
    assert len(run("http_headers", "tracking.acme-shipping.example.test", ctx, options)) == 1


def test_tls_expiry_follows_the_context_clock(ctx, make_ctx):
    [soon] = run("tls_config", "tracking.acme-shipping.example.test", ctx)
    assert (soon.rule_id, soon.location, soon.evidence["days_left"]) == (
        "cert-expiring-soon", "tls:443", 16,
    )
    later = make_ctx(at=datetime(2026, 11, 1, tzinfo=UTC))
    assert rules(run("tls_config", "tracking.acme-shipping.example.test", later)) == {
        ("cert-expired", "tls:443")
    }
    options = {"expiry_warning_days": 7}
    assert run("tls_config", "tracking.acme-shipping.example.test", ctx, options) == []


def test_tls_legacy_host_reports_every_weakness(ctx):
    found = run("tls_config", "legacy-intranet.northpeak-health.example.test", ctx, kind="host")
    assert {r.rule_id for r in found} == {
        "cert-hostname-mismatch", "cert-self-signed", "legacy-protocol", "no-tls13", "weak-cipher",
        "weak-key",
    }
    assert {r.severity for r in found if r.rule_id.startswith("cert-")} == {"error"}


@pytest.mark.parametrize(
    ("pattern", "hostname", "expected"),
    [("*.lumen-retail.example.test", "shop.lumen-retail.example.test", True),
     ("*.lumen-retail.example.test", "a.shop.lumen-retail.example.test", False),
     ("*.lumen-retail.example.test", "lumen-retail.example.test", False),
     ("Shop.Lumen-Retail.example.test.", "shop.lumen-retail.example.test", True)],
)
def test_hostname_matches(pattern, hostname, expected):
    assert hostname_matches(pattern, hostname) is expected


def test_open_ports_against_tenant_policy(ctx):
    assert rules(run("open_ports", "portal.northpeak-health.example.test", ctx)) == {
        ("unexpected-open-port", "port:80/tcp")
    }
    assert run("open_ports", "portal.northpeak-health.example.test", ctx, allowed=(80, 443)) == []
    ftp = run("open_ports", "ftp.acme-shipping.example.test", ctx, {"ignore": [22]})
    assert [(r.rule_id, r.severity, r.location) for r in ftp] == [
        ("risky-service-exposed", "error", "port:21/tcp")
    ]
    with pytest.raises(PluginError):
        run("open_ports", "ftp.acme-shipping.example.test", ctx, {"ignore": ["22"]})


@pytest.fixture
def config_root(tmp_path):
    configs = tmp_path / "assets" / "app.example.test" / "configs"
    (configs / "config").mkdir(parents=True)
    (configs / "config" / "app.env").write_text(
        "DB_PASSWORD=${DB_PASSWORD}\n"
        "ADMIN_PASSWORD=DEMO-admin-secret-1\n"
        "DATABASE_URL=postgres://app:DEMO-pg-pass@db:5432/app\n"
        "SEARCH_API_KEY='DEMO_search_00112233445566'\n"
    )
    (configs / "deploy").mkdir()
    (configs / "deploy" / "key.pem").write_text("-----BEGIN DEMO PRIVATE KEY-----\nDEMOx\n")
    (configs / "deploy" / "blob.bin").write_bytes(b"password=DEMO-binary\x00")
    return tmp_path


def test_secrets_are_found_and_redacted(config_root, make_ctx):
    found = run("secrets_in_config", "app.example.test", make_ctx(config_root), allowed=())
    assert rules(found) == {
        ("hardcoded-password", "file:config/app.env:2"),
        ("connection-string-password", "file:config/app.env:3"),
        ("generic-api-key", "file:config/app.env:4"),
        ("private-key", "file:deploy/key.pem:1"),
    }
    dumped = json.dumps([r.evidence for r in found])
    for secret in ("DEMO-admin-secret-1", "DEMO-pg-pass", "DEMO_search_00112233445566"):
        assert secret not in dumped
    assert {r.evidence["match"] for r in found} >= {"DEMO********"}


def test_secrets_paths_option_and_missing_data(config_root, make_ctx, ctx):
    local = make_ctx(config_root)
    scoped = run("secrets_in_config", "app.example.test", local, {"paths": ["deploy/"]}, allowed=())
    assert [r.rule_id for r in scoped] == ["private-key"]
    with pytest.raises(PluginDataMissing):
        run("secrets_in_config", "app.example.test", local, {"paths": ["k8s/"]}, allowed=())
    with pytest.raises(PluginDataMissing):
        run("secrets_in_config", "status.northpeak-health.example.test", ctx)
    with pytest.raises(PluginError, match="invalid hostname"):
        run("secrets_in_config", "..", ctx, allowed=())

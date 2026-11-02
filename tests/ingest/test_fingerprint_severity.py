from __future__ import annotations

import logging

import pytest

from app.ingest.fingerprint import compute_fingerprint, normalize_location
from app.ingest.severity import map_severity
from app.models.enums import Severity

BASE = (1, 2, "secrets_in_config", "hardcoded-password", "file:config/app.env:4")


def test_fingerprint_is_sha256_and_uses_every_field_including_location():
    fp = compute_fingerprint(*BASE)
    assert len(fp) == 64 and int(fp, 16) >= 0
    assert compute_fingerprint(*BASE) == fp
    others = (9, 9, "http_headers", "generic-api-key", "file:config/app.env:5")
    for index, other in enumerate(others):
        changed = list(BASE)
        changed[index] = other
        assert compute_fingerprint(*changed) != fp


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("header:Strict-Transport-Security ", "header:strict-transport-security"),
        ("HEADER: X-Frame-Options", "header:x-frame-options"),
        ("port:443/TCP", "port:443/tcp"),
        ("package:npm:Harborlight-Router@3.1.4", "package:npm:harborlight-router@3.1.4"),
        ("file:./config//App.env:12", "file:config/App.env:12"),
        ("file:config\\app.env:3", "file:config/app.env:3"),
    ],
)
def test_location_normalization(raw, normalized):
    assert normalize_location(raw) == normalized
    fingerprint = compute_fingerprint(1, 2, "p", "r", normalized)
    assert compute_fingerprint(1, 2, "p", "r", raw) == fingerprint


@pytest.mark.parametrize(
    ("plugin", "native", "expected"),
    [("http_headers", "note", Severity.INFO), ("http_headers", "warning", Severity.LOW),
     ("tls_config", "warning", Severity.MEDIUM), ("tls_config", "error", Severity.HIGH),
     ("open_ports", "error", Severity.HIGH), ("dependency_audit", "moderate", Severity.MEDIUM),
     ("dependency_audit", "critical", Severity.CRITICAL),
     ("secrets_in_config", "high", Severity.HIGH), ("cookie_flags", "Critical", Severity.CRITICAL)],
)
def test_severity_table(plugin, native, expected):
    assert map_severity(plugin, native) is expected


def test_unknown_severity_falls_back_to_medium_and_logs(caplog):
    with caplog.at_level(logging.WARNING, logger="app.ingest.severity"):
        assert map_severity("open_ports", "note") is Severity.MEDIUM
    assert [r.getMessage() for r in caplog.records] == ["ingest.unknown_severity"]

import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.core import clock
from app.core.contracts import AssetSnapshot, IngestSummary, Plugin, PluginContext, RawResult
from app.core.errors import PluginDataMissing, PluginTimeout, TidewrightError


def _result(**overrides):
    fields = {
        "plugin": "http_headers",
        "rule_id": "missing-hsts",
        "title": "Strict-Transport-Security header missing",
        "severity": "warning",
        "location": "header:strict-transport-security",
        "evidence": {"status": 200},
        "asset_id": 7,
    }
    fields.update(overrides)
    return RawResult(**fields)


def test_raw_result_round_trips_to_dict():
    result = _result()
    assert RawResult(**result.to_dict()) == result


@pytest.mark.parametrize(
    "overrides",
    [
        {"plugin": ""},
        {"rule_id": "  "},
        {"location": ""},
        {"severity": "HIGH"},
        {"evidence": ["not", "a", "dict"]},
        {"asset_id": 0},
    ],
)
def test_raw_result_validates_fields(overrides):
    with pytest.raises(ValueError):
        _result(**overrides)


def test_asset_snapshot_from_models(factory):
    tenant = factory.tenant(allowed_ports=[443, 80])
    asset = factory.asset(tenant, tags=["prod"])
    snap = AssetSnapshot.from_models(asset, tenant)
    assert snap.hostname == asset.hostname
    assert snap.allowed_ports == (80, 443)
    assert snap.tags == ("prod",)
    with pytest.raises(ValueError):
        AssetSnapshot.from_models(asset, factory.tenant())


class _EchoPlugin:
    name = "echo"
    version = "1.0.0"

    def run(self, asset, options, context):
        return [
            _result(
                plugin=self.name,
                asset_id=asset.id,
                evidence={"at": clock.isoformat(context.now())},
            )
        ]


def test_plugin_protocol_and_context(tmp_path: Path, frozen_clock):
    plugin = _EchoPlugin()
    assert isinstance(plugin, Plugin)
    ctx = PluginContext(fixtures_root=tmp_path, logger=logging.getLogger("t"))
    assert ctx.asset_dir("shop.acme-shipping.example.test") == (
        tmp_path / "assets" / "shop.acme-shipping.example.test"
    )
    snap = AssetSnapshot(id=3, tenant_id=1, hostname="h.example.test", kind="web")
    [result] = plugin.run(snap, {}, ctx)
    assert result.evidence == {"at": "2026-10-14T09:00:00Z"}
    assert ctx.now() == datetime(2026, 10, 14, 9, tzinfo=UTC)


def test_ingest_summary_counts():
    summary = IngestSummary(scan_id=1, created=[1, 2], seen=[3], fixed=[4], duplicates=2)
    assert summary.counts() == {"created": 2, "seen": 1, "reopened": 0, "fixed": 1, "duplicates": 2}
    assert summary.total_results == 5


def test_plugin_errors_serialize_for_the_api():
    err = PluginTimeout("tls_config", 2.5)
    assert isinstance(err, TidewrightError)
    assert err.to_dict()["error"]["code"] == "plugin_timeout"
    assert err.to_dict()["error"]["details"] == {"plugin": "tls_config", "timeout_s": 2.5}
    assert PluginDataMissing("open_ports", "no ports.json").plugin == "open_ports"

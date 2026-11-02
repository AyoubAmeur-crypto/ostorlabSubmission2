from __future__ import annotations

import dataclasses
import json
import threading

import pytest

from app.core.errors import PluginDataMissing, PluginError, PluginTimeout, UnknownPlugin
from app.plugins import registry
from tests.plugins.conftest import FIXTURES, snapshot


class FakePlugin:
    version = "0.0.1"

    def __init__(self, run, name: str = "fake") -> None:
        self.name = name
        self._run = run

    def run(self, asset, options, context):
        return self._run(asset)


def test_available_lists_the_builtin_plugins():
    assert registry.available() == [
        "dependency_audit", "http_headers", "open_ports", "secrets_in_config", "tls_config",
    ]
    assert registry.get_plugin("tls_config").name == "tls_config"
    with pytest.raises(UnknownPlugin):
        registry.get_plugin("cookie_flags")
    with pytest.raises(ValueError):
        registry.register(FakePlugin(lambda a: [], name="http_headers"))


def test_timeout_raises_plugin_timeout(ctx):
    release = threading.Event()
    slow = FakePlugin(lambda a: release.wait(5) and [])
    asset = snapshot("www.acme-shipping.example.test")
    try:
        with pytest.raises(PluginTimeout) as info:
            registry.run_with_timeout(slow, asset, {}, ctx, 0.05)
        assert info.value.timeout_s == 0.05
    finally:
        release.set()


def test_unexpected_errors_are_wrapped_and_plugin_errors_pass_through(ctx):
    def boom(asset):
        raise KeyError("port")

    asset = snapshot("www.acme-shipping.example.test")
    with pytest.raises(PluginError) as info:
        registry.run_with_timeout(FakePlugin(boom), asset, {}, ctx, 1)
    assert type(info.value) is PluginError and isinstance(info.value.__cause__, KeyError)
    host = snapshot("ftp.acme-shipping.example.test", kind="host")
    with pytest.raises(PluginDataMissing):
        registry.run_with_timeout(registry.get_plugin("http_headers"), host, {}, ctx, 1)


def test_results_for_another_asset_are_rejected(ctx):
    other = snapshot("tracking.acme-shipping.example.test")
    plugin = registry.get_plugin("http_headers")
    leaked = FakePlugin(lambda a: plugin.run(other, {}, ctx), name="http_headers")
    asset = dataclasses.replace(snapshot("www.acme-shipping.example.test"), id=8)
    with pytest.raises(PluginError, match="claims"):
        registry.run_with_timeout(leaked, asset, {}, ctx, 1)


@pytest.mark.parametrize("hostname", sorted(p.name for p in (FIXTURES / "assets").iterdir()))
def test_every_fixture_asset_is_deterministic(ctx, hostname):
    asset = snapshot(hostname)
    for name in registry.available():
        plugin = registry.get_plugin(name)
        try:
            first = registry.run_with_timeout(plugin, asset, {}, ctx, 5)
        except PluginDataMissing:
            continue
        assert first == registry.run_with_timeout(plugin, asset, {}, ctx, 5)
        json.dumps([r.to_dict() for r in first])

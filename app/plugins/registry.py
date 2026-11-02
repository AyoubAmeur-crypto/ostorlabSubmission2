"""Plugin registry and the per-plugin timeout used by the scan pipeline.

Plugins are registered explicitly (no entry-point scanning): adding a check
means writing the module and listing its class in ``_BUILTIN``. See
docs/architecture.md section 4.2 for the contract.
"""

from __future__ import annotations

import contextvars
import threading
from collections.abc import Mapping
from typing import Any

from app.core.contracts import AssetSnapshot, Plugin, PluginContext, RawResult
from app.core.errors import PluginError, PluginTimeout, UnknownPlugin
from app.plugins.dependency_audit import DependencyAuditPlugin
from app.plugins.http_headers import HttpHeadersPlugin
from app.plugins.open_ports import OpenPortsPlugin
from app.plugins.secrets_in_config import SecretsInConfigPlugin
from app.plugins.tls_config import TlsConfigPlugin

_BUILTIN: tuple[type, ...] = (
    HttpHeadersPlugin, TlsConfigPlugin, OpenPortsPlugin,
    DependencyAuditPlugin, SecretsInConfigPlugin,
)

_registry: dict[str, Plugin] = {cls.name: cls() for cls in _BUILTIN}


def register(plugin: Plugin, *, replace: bool = False) -> None:
    """Add a plugin instance under ``plugin.name`` (tests, local experiments)."""
    if not isinstance(plugin, Plugin):
        raise TypeError(f"{plugin!r} does not implement the Plugin protocol")
    if plugin.name in _registry and not replace:
        raise ValueError(f"plugin {plugin.name!r} is already registered")
    _registry[plugin.name] = plugin


def get_plugin(name: str) -> Plugin:
    try:
        return _registry[name]
    except KeyError:
        raise UnknownPlugin(name) from None


def available() -> list[str]:
    return sorted(_registry)


def run_with_timeout(
    plugin: Plugin,
    asset: AssetSnapshot,
    options: Mapping[str, Any],
    context: PluginContext,
    timeout_s: float,
) -> list[RawResult]:
    """Run ``plugin`` in a worker thread and wait at most ``timeout_s`` seconds.

    Python threads cannot be killed: on timeout the thread is left to finish in
    the background (daemon, so it never blocks shutdown) and its output is
    discarded. Simulated plugins only read files, so that is harmless.
    """
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")
    outcome: dict[str, Any] = {}

    def target() -> None:
        try:
            outcome["results"] = plugin.run(asset, options, context)
        except Exception as exc:  # noqa: BLE001 - re-raised in the caller's thread
            outcome["error"] = exc

    # copy_context keeps log fields bound by the worker (scan_id, job_id).
    ctx = contextvars.copy_context()
    thread = threading.Thread(
        target=ctx.run, args=(target,), name=f"plugin-{plugin.name}-{asset.id}", daemon=True
    )
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        context.logger.warning(
            "plugin.timeout",
            extra={"plugin": plugin.name, "asset_id": asset.id, "timeout_s": timeout_s},
        )
        raise PluginTimeout(plugin.name, timeout_s)

    error = outcome.get("error")
    if isinstance(error, PluginError):
        raise error
    if error is not None:
        context.logger.error(
            "plugin.crashed", extra={"plugin": plugin.name, "asset_id": asset.id}, exc_info=error
        )
        raise PluginError(plugin.name, f"unexpected {type(error).__name__}") from error
    return _checked(plugin, asset, outcome.get("results"))


def _checked(plugin: Plugin, asset: AssetSnapshot, results: Any) -> list[RawResult]:
    if not isinstance(results, list):
        raise PluginError(plugin.name, "run() must return a list of RawResult")
    for item in results:
        if not isinstance(item, RawResult):
            raise PluginError(plugin.name, f"run() returned {type(item).__name__}, not RawResult")
        if item.plugin != plugin.name or item.asset_id != asset.id:
            raise PluginError(
                plugin.name,
                f"result {item.rule_id} claims plugin {item.plugin!r} / asset {item.asset_id}",
            )
    return results

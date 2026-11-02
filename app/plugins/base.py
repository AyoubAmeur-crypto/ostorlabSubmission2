"""Shared plumbing for the simulated plugins: fixture access and options.

A missing fixture raises ``PluginDataMissing``; an empty result list means
"checked, nothing found" (and lets ingest mark findings fixed).
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from app.core.contracts import AssetSnapshot, PluginContext, RawResult
from app.core.errors import PluginDataMissing, PluginError


class SimulatedPlugin(ABC):
    """Base class: subclasses implement :meth:`check`; :meth:`run` applies the
    common ``ignore_rules`` option and sorts results so output is stable."""

    name: ClassVar[str]
    version: ClassVar[str]

    def run(
        self, asset: AssetSnapshot, options: Mapping[str, Any], context: PluginContext
    ) -> list[RawResult]:
        ignored = set(self.str_list(options, "ignore_rules"))
        results = [r for r in self.check(asset, options, context) if r.rule_id not in ignored]
        return sorted(results, key=lambda r: (r.location, r.rule_id))

    @abstractmethod
    def check(
        self, asset: AssetSnapshot, options: Mapping[str, Any], context: PluginContext
    ) -> list[RawResult]: ...

    # helpers -----------------------------------------------------------

    def result(
        self,
        asset: AssetSnapshot,
        rule_id: str,
        title: str,
        severity: str,
        location: str,
        evidence: dict[str, Any],
    ) -> RawResult:
        return RawResult(
            plugin=self.name,
            rule_id=rule_id,
            title=title[:300],
            severity=severity,
            location=location,
            evidence=evidence,
            asset_id=asset.id,
        )

    def asset_root(self, asset: AssetSnapshot, context: PluginContext) -> Path:
        """The asset's recorded-data directory. Refuses hostnames that would
        resolve outside ``fixtures/assets/`` (``..``, slashes)."""
        base = (context.fixtures_root / "assets").resolve()
        root = context.asset_dir(asset.hostname).resolve()
        if root.parent != base:
            raise PluginError(self.name, f"invalid hostname for fixtures: {asset.hostname!r}")
        return root

    def load_json(self, asset: AssetSnapshot, context: PluginContext, filename: str) -> Any:
        path = self.asset_root(asset, context) / filename
        if not path.is_file():
            raise PluginDataMissing(self.name, f"no recorded {filename} for {asset.hostname}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise PluginError(self.name, f"malformed {filename} for {asset.hostname}") from None

    def str_list(self, options: Mapping[str, Any], key: str) -> list[str]:
        value = options.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise PluginError(self.name, f"option {key!r} must be a list of strings")
        return value

    def int_option(self, options: Mapping[str, Any], key: str, default: int) -> int:
        value = options.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PluginError(self.name, f"option {key!r} must be a non-negative integer")
        return value

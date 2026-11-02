"""Cross-component contracts: what plugins produce and what ingest reports.

The pipeline for one scan (owned by ``app.worker``)::

    asset row --AssetSnapshot.from_models()--> AssetSnapshot
    for each plugin in profile.plugins:
        plugin = app.plugins.registry.get_plugin(name)
        results += app.plugins.registry.run_with_timeout(plugin, snapshot, options, ctx, timeout_s)
    app.ingest.service.ingest_scan_results(session, scan, results, completed_plugins=[...])
        -> IngestSummary

These types are deliberately free of SQLAlchemy and FastAPI so plugins can be
unit-tested without a database. See docs/architecture.md, "Contracts", for the
full list of public functions each component exposes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from app.core import clock as clock_module

if TYPE_CHECKING:
    from app.models.asset import Asset
    from app.models.tenant import Tenant


@dataclass(frozen=True, slots=True)
class AssetSnapshot:
    """Read-only view of an asset handed to plugins.

    Plugins run in a worker thread with a timeout and must not touch the
    database, so they get this instead of the ORM row.
    """

    id: int
    tenant_id: int
    hostname: str
    kind: str
    tags: tuple[str, ...] = ()
    # The owning tenant's allowed ports (used by open_ports).
    allowed_ports: tuple[int, ...] = ()

    @classmethod
    def from_models(cls, asset: Asset, tenant: Tenant) -> AssetSnapshot:
        if asset.tenant_id != tenant.id:
            raise ValueError(f"asset {asset.id} does not belong to tenant {tenant.id}")
        return cls(
            id=asset.id,
            tenant_id=asset.tenant_id,
            hostname=asset.hostname,
            kind=asset.kind,
            tags=tuple(str(t) for t in (asset.tags or [])),
            allowed_ports=tuple(sorted(tenant.allowed_ports or [])),
        )


@dataclass(frozen=True, slots=True)
class RawResult:
    """One observation from one plugin about one asset, before normalization.

    plugin    registry name of the plugin that produced it (``http_headers``).
    rule_id   stable identifier of the check inside the plugin
              (``missing-hsts``, ``GHSA-xxxx-...``). Never contains run-specific data.
    title     short human sentence; may mention the location.
    severity  the plugin's *native* severity label (see docs/architecture.md,
              "Severity vocabularies"); ingest maps it to a Severity bucket.
    location  where on the asset, in the plugin's documented format
              (``header:strict-transport-security``, ``port:23/tcp``,
              ``file:config/app.env:12``, ``package:npm:lodash@4.17.20``).
              Part of the fingerprint, so it must be stable between runs.
    evidence  JSON-serializable details for humans. Secrets must already be
              redacted by the plugin.
    asset_id  ``AssetSnapshot.id`` of the asset the plugin was run against.
    """

    plugin: str
    rule_id: str
    title: str
    severity: str
    location: str
    evidence: dict[str, Any]
    asset_id: int

    def __post_init__(self) -> None:
        for name in ("plugin", "rule_id", "title", "severity", "location"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"RawResult.{name} must be a non-empty string")
        if self.severity != self.severity.lower():
            raise ValueError("RawResult.severity must be lower case")
        if not isinstance(self.evidence, dict):
            raise ValueError("RawResult.evidence must be a dict")
        if not isinstance(self.asset_id, int) or self.asset_id <= 0:
            raise ValueError("RawResult.asset_id must be a positive int")

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin": self.plugin,
            "rule_id": self.rule_id,
            "title": self.title,
            "severity": self.severity,
            "location": self.location,
            "evidence": self.evidence,
            "asset_id": self.asset_id,
        }


@dataclass(frozen=True, slots=True)
class PluginContext:
    """Everything a plugin may use besides its options.

    fixtures_root  root of the recorded data (``fixtures/``); per-asset files
                   live under ``fixtures_root / "assets" / <hostname>``.
    clock          returns aware UTC now; use instead of datetime.now().
    logger         already bound to the plugin; add ``extra`` fields freely.
    scan_id        the scan being run, for log correlation (None in unit tests).
    """

    fixtures_root: Path
    clock: Callable[[], datetime] = clock_module.now
    logger: logging.Logger | logging.LoggerAdapter[logging.Logger] = field(
        default_factory=lambda: logging.getLogger("app.plugins")
    )
    scan_id: int | None = None

    def asset_dir(self, hostname: str) -> Path:
        return self.fixtures_root / "assets" / hostname

    def now(self) -> datetime:
        return self.clock()

    @classmethod
    def default(cls, *, scan_id: int | None = None) -> PluginContext:
        from app.core.config import get_settings

        return cls(fixtures_root=get_settings().fixtures_root, scan_id=scan_id)


@runtime_checkable
class Plugin(Protocol):
    """A simulated security check. Implementations are deterministic, never use
    the network, and read only files under ``context.fixtures_root``.

    ``run`` returns every observation for the asset (an empty list means "ran
    fine, nothing to report", which lets ingest mark earlier findings fixed).
    When the recorded data needed is absent it raises
    :class:`app.core.errors.PluginDataMissing`; any other inability to finish
    raises :class:`app.core.errors.PluginError`. Either way ingest is not
    called for that plugin, so nothing is marked fixed by mistake.
    """

    name: str
    version: str

    def run(
        self, asset: AssetSnapshot, options: Mapping[str, Any], context: PluginContext
    ) -> list[RawResult]: ...


@dataclass(slots=True)
class IngestSummary:
    """What one ``ingest_scan_results`` call changed. Lists hold finding ids.

    created     findings seen for the first time (status ``new``).
    seen        existing open/new/reopened findings observed again.
    reopened    findings that were ``fixed`` and showed up again.
    fixed       findings of a completed plugin that were absent this time.
    duplicates  results dropped because an identical fingerprint appeared
                earlier in the same batch.
    """

    scan_id: int
    created: list[int] = field(default_factory=list)
    seen: list[int] = field(default_factory=list)
    reopened: list[int] = field(default_factory=list)
    fixed: list[int] = field(default_factory=list)
    duplicates: int = 0

    @property
    def total_results(self) -> int:
        return len(self.created) + len(self.seen) + len(self.reopened) + self.duplicates

    def counts(self) -> dict[str, int]:
        """Flat counts for logs and the ``scan.completed`` webhook payload."""
        return {
            "created": len(self.created),
            "seen": len(self.seen),
            "reopened": len(self.reopened),
            "fixed": len(self.fixed),
            "duplicates": self.duplicates,
        }

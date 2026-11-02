"""Ingest fixtures: a recording stand-in for ``app.reporting.outbox`` and a
small world (tenant, asset, profile) with helpers to run one scan."""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.core.contracts import IngestSummary, RawResult
from app.ingest.service import ingest_scan_results
from app.models import Asset, Scan, ScanProfile, Tenant


@pytest.fixture
def outbox(monkeypatch) -> list[tuple[str, dict[str, Any]]]:
    """``(event, payload)`` for every enqueue_event call made by ingest."""
    sent: list[tuple[str, dict[str, Any]]] = []

    def enqueue_event(session, tenant_id, event, payload) -> int:
        sent.append((str(event), payload))
        return 1

    fake = types.ModuleType("app.reporting.outbox")
    fake.enqueue_event = enqueue_event  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.reporting.outbox", fake)
    return sent


@dataclass
class World:
    session: Any
    factory: Any
    tenant: Tenant
    asset: Asset
    profile: ScanProfile
    scans: list[Scan] = field(default_factory=list)

    def raw(self, rule_id="missing-hsts", location="header:strict-transport-security", *,
            plugin="http_headers", severity="warning", title=None) -> RawResult:
        return RawResult(plugin=plugin, rule_id=rule_id, title=title or f"{rule_id} title",
                         severity=severity, location=location, evidence={"seen": True},
                         asset_id=self.asset.id)

    def scan(self, results, completed=("http_headers", "tls_config")) -> IngestSummary:
        scan = self.factory.scan(asset=self.asset, profile=self.profile, status="running")
        self.scans.append(scan)
        return ingest_scan_results(self.session, scan, results, completed_plugins=list(completed))


@pytest.fixture
def world(db_session, factory, frozen_clock, outbox) -> World:
    tenant = factory.tenant(slug="acme-shipping", timezone="Europe/Amsterdam")
    asset = factory.asset(tenant, hostname="tracking.acme-shipping.example.test")
    profile = factory.profile(tenant, plugins=["http_headers", "tls_config"])
    return World(db_session, factory, tenant, asset, profile)

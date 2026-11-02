"""Stand-ins for plugins, ingest and reporting. The worker imports them at call
time, so modules in ``sys.modules`` are enough to keep these tests about the worker."""

from __future__ import annotations

import sys
import types
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.core.contracts import IngestSummary, RawResult
from app.core.errors import UnknownPlugin


@dataclass
class Fakes:
    # plugin name -> rule ids it reports, or the exception it raises
    plugins: dict[str, list[str] | Exception] = field(
        default_factory=lambda: {"http_headers": ["missing-hsts"], "tls_config": []}
    )
    before_plugin: Callable[[str], None] | None = None
    ingest_error: Exception | None = None
    report_error: Exception | None = None
    ran: list[str] = field(default_factory=list)
    ingested: list[tuple[int, list[str], list[str]]] = field(default_factory=list)
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    reports: list[int] = field(default_factory=list)
    dispatched: int = 0


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> Fakes:
    fake = Fakes()

    def get_plugin(name):
        if name not in fake.plugins:
            raise UnknownPlugin(name)
        return types.SimpleNamespace(name=name, version="0-test")

    def run_with_timeout(plugin, asset, options, context, timeout_s):
        if fake.before_plugin:
            fake.before_plugin(plugin.name)
        fake.ran.append(plugin.name)
        outcome = fake.plugins[plugin.name]
        if isinstance(outcome, Exception):
            raise outcome
        return [
            RawResult(plugin.name, rule, rule, "warning", f"header:{rule}", {}, asset.id)
            for rule in outcome
        ]

    def ingest_scan_results(session, scan, results, *, completed_plugins):
        if fake.ingest_error:
            raise fake.ingest_error
        fake.ingested.append((scan.id, [r.rule_id for r in results], list(completed_plugins)))
        return IngestSummary(scan_id=scan.id)

    def enqueue_event(session, tenant_id, event, payload):
        fake.events.append((str(event), payload))
        return 1

    def generate_report(session, report_id):
        if fake.report_error:
            raise fake.report_error
        fake.reports.append(report_id)
        return f"var/reports/report-{report_id}.json"

    def dispatch_pending(session, *, limit=50, client=None):
        fake.dispatched += 1
        return 0

    fns = (get_plugin, run_with_timeout, ingest_scan_results, enqueue_event, generate_report)
    stub = types.SimpleNamespace(**{fn.__name__: fn for fn in (*fns, dispatch_pending)})
    for name in ("plugins.registry", "ingest.service", "reporting.outbox", "reporting.service",
                 "reporting.webhooks"):
        monkeypatch.setitem(sys.modules, f"app.{name}", stub)
    return fake

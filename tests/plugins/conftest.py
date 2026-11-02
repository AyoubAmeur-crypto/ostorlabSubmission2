"""Plugin tests run against the recorded fixtures in ``fixtures/`` (no database)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from app.core.config import REPO_ROOT
from app.core.contracts import AssetSnapshot, PluginContext, RawResult
from tests.factories import TODAY

FIXTURES = REPO_ROOT / "fixtures"
TENANT_PORTS = {
    "acme-shipping": (80, 443), "northpeak-health": (443,), "lumen-retail": (80, 443, 8443)
}


def snapshot(hostname: str, *, kind: str = "web", allowed: tuple | None = None) -> AssetSnapshot:
    ports = TENANT_PORTS[hostname.split(".")[1]] if allowed is None else allowed
    return AssetSnapshot(id=7, tenant_id=3, hostname=hostname, kind=kind, allowed_ports=ports)


def rules(results: list[RawResult]) -> set[tuple[str, str]]:
    return {(r.rule_id, r.location) for r in results}


@pytest.fixture
def ctx() -> PluginContext:
    return PluginContext(fixtures_root=FIXTURES, clock=lambda: TODAY)


@pytest.fixture
def make_ctx() -> Callable[..., PluginContext]:
    def build(root=FIXTURES, at=TODAY) -> PluginContext:
        return PluginContext(fixtures_root=root, clock=lambda: at)

    return build

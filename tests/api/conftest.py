"""API test fixtures. These tests cover the HTTP layer: ``services`` swaps the
functions the API calls in other components (request_scan, request_report, the
plugin registry, parse_cron) for small fakes that follow docs/architecture.md s4."""

from __future__ import annotations

import re
import sys
import types
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
import redis
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.ratelimit import FixedWindowRateLimiter
from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.models import Asset, Report, Scan, ScanProfile, Tenant

PLUGINS = ["dependency_audit", "http_headers", "open_ports", "secrets_in_config", "tls_config"]


def _request_scan(session, *, tenant_id, profile_id, asset_id, trigger, scheduled_slot=None):
    profile = session.get(ScanProfile, profile_id)
    asset = session.get(Asset, asset_id)
    if profile is None or profile.tenant_id != tenant_id:
        raise NotFound.for_resource("profile", profile_id)
    if asset is None or asset.tenant_id != tenant_id:
        raise NotFound.for_resource("asset", asset_id)
    if asset.archived_at is not None or not profile.enabled:
        raise Conflict("asset archived or profile disabled")
    scan = Scan(tenant_id=tenant_id, profile_id=profile_id, asset_id=asset_id, trigger=trigger)
    session.add(scan)
    session.flush()
    return scan


def _request_report(session, tenant_id, kind, filters):
    report = Report(tenant_id=tenant_id, kind=str(kind), filters=filters.to_column())
    session.add(report)
    session.flush()
    return report


def _parse_cron(expr):
    if len(expr.split()) != 5 or not all(re.fullmatch(r"[\d*,/-]+", f) for f in expr.split()):
        raise ValidationFailed(f"invalid cron expression {expr!r}")
    return expr


@pytest.fixture(autouse=True)
def services(monkeypatch: pytest.MonkeyPatch) -> None:
    fakes = {
        "app.worker.scans": {"request_scan": _request_scan},
        "app.reporting.service": {"request_report": _request_report},
        "app.plugins.registry": {"available": lambda: list(PLUGINS)},
        "app.scheduler.cron": {"parse_cron": _parse_cron},
    }
    for name, attrs in fakes.items():
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)


@pytest.fixture
def limiter() -> Iterator[FixedWindowRateLimiter]:
    client = redis.Redis.from_url(get_settings().redis_url)
    prefix = f"tw:test:api:{uuid.uuid4().hex[:8]}"
    yield FixedWindowRateLimiter(client, prefix=prefix)
    for key in client.scan_iter(f"{prefix}:*"):
        client.delete(key)
    client.close()


@pytest.fixture
def client(db_session, limiter) -> TestClient:
    return TestClient(create_app(rate_limiter=limiter))


@dataclass
class Caller:
    tenant: Tenant
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def _caller(factory, slug: str) -> Caller:
    tenant = factory.tenant(slug=slug)
    return Caller(tenant, factory.token(tenant)[1])


@pytest.fixture
def acme(factory) -> Caller:
    return _caller(factory, "acme-shipping")


@pytest.fixture
def northpeak(factory) -> Caller:
    return _caller(factory, "northpeak-health")


@pytest.fixture
def admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_settings().admin_token}"}

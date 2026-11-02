from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas import (
    AssetCreate,
    FindingEventData,
    FindingFilters,
    FindingRead,
    ReportCreate,
    ScanEventData,
    ScanProfileCreate,
    TenantCreate,
    WebhookCreate,
)


def test_tenant_create_validates_slug_zone_and_ports():
    tenant = TenantCreate(
        slug="lumen-retail",
        name="Lumen Retail",
        timezone="America/New_York",
        allowed_ports=[443, 80, 443],
    )
    assert tenant.allowed_ports == [80, 443]
    with pytest.raises(ValidationError):
        TenantCreate(slug="Lumen_Retail", name="x")
    with pytest.raises(ValidationError):
        TenantCreate(slug="lumen", name="x", timezone="Europe/Atlantis")
    with pytest.raises(ValidationError):
        TenantCreate(slug="lumen", name="x", allowed_ports=[70000])


def test_inputs_reject_unknown_fields():
    with pytest.raises(ValidationError):
        AssetCreate(hostname="a.example.test", owner="me")


def test_asset_tags_are_normalized():
    asset = AssetCreate(hostname="Shop.Acme-Shipping.example.test.", tags=["Prod", "prod", "pci"])
    assert asset.tags == ["prod", "pci"]
    # Hostname normalization is the API's job; the schema keeps the raw value.
    assert asset.hostname == "Shop.Acme-Shipping.example.test."


def test_profile_shape_checks():
    profile = ScanProfileCreate(
        name="nightly", plugins=["http_headers", "tls_config"], schedule_cron="0  2 * * *"
    )
    assert profile.schedule_cron == "0 2 * * *"
    assert profile.options.to_column() == {"plugins": {}}
    with pytest.raises(ValidationError):
        ScanProfileCreate(name="x", plugins=["http_headers", "http_headers"])
    with pytest.raises(ValidationError):
        ScanProfileCreate(name="x", plugins=["http_headers"], schedule_cron="0 2 * *")
    with pytest.raises(ValidationError):
        ScanProfileCreate(name="x", plugins=[])


def test_finding_filters_require_aware_since():
    filters = FindingFilters(severity=["high", "critical"], since="2026-10-01T00:00:00+02:00")
    assert filters.since == datetime(2026, 9, 30, 22, tzinfo=UTC)
    assert filters.to_column() == {
        "severity": ["high", "critical"],
        "since": "2026-09-30T22:00:00Z",
    }
    with pytest.raises(ValidationError):
        FindingFilters(since="2026-10-01T00:00:00")
    with pytest.raises(ValidationError):
        FindingFilters(severity=["urgent"])


def test_report_create_defaults_to_no_filters():
    report = ReportCreate(kind="csv")
    assert report.filters.to_column() == {}


def test_webhook_create_validates_events_and_url():
    hook = WebhookCreate(
        url="http://mockhook:9000/hooks/acme",
        secret="DEMO-whsec-0123456789",
        events=["scan.completed", "finding.new", "finding.new"],
    )
    assert [e.value for e in hook.events] == ["finding.new", "scan.completed"]
    with pytest.raises(ValidationError):
        WebhookCreate(
            url="ftp://x.example.test", secret="DEMO-whsec-0123456789", events=["finding.new"]
        )
    with pytest.raises(ValidationError):
        WebhookCreate(
            url="http://x.example.test", secret="DEMO-whsec-0123456789", events=["finding.deleted"]
        )


def test_read_schemas_and_event_payloads_from_rows(factory, frozen_clock):
    scan = factory.scan(
        status="completed", started_at=frozen_clock.now(), finished_at=frozen_clock.now()
    )
    finding = factory.finding(scan=scan, severity="high")
    read = FindingRead.model_validate(finding)
    assert read.severity == "high" and read.scan_id_last == scan.id

    data = FindingEventData.from_finding(finding).model_dump(mode="json")
    assert data["first_seen_at"] == "2026-10-14T09:00:00Z"
    assert data["fixed_at"] is None

    scan_data = ScanEventData.from_scan(scan, {"created": 1}).model_dump(mode="json")
    assert scan_data["status"] == "completed"
    assert scan_data["counts"] == {"created": 1}

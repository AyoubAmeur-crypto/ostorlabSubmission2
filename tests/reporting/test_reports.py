import csv
import io
import json
import sys
import types
from pathlib import Path

import pytest

from app.core.config import override_settings
from app.reporting import service
from app.reporting.legacy_csv import write_findings_csv
from app.schemas.finding import FindingFilters


@pytest.fixture
def fake_queue(monkeypatch):
    calls = []
    module = types.ModuleType("app.worker.queue")
    module.enqueue = lambda session, kind, payload, **kw: calls.append((str(kind), payload))
    monkeypatch.setitem(sys.modules, "app.worker.queue", module)
    return calls


def test_request_report_creates_row_and_job(factory, fake_queue):
    tenant = factory.tenant()
    report = service.request_report(factory.session, tenant.id, "csv",
                                    FindingFilters(severity=["high"]))
    assert (report.status, report.kind) == ("pending", "csv")
    assert report.filters == {"severity": ["high"]}
    assert fake_queue == [("report.generate", {"report_id": report.id})]


def test_csv_report_applies_filters_and_escapes(factory, tmp_path):
    tenant = factory.tenant()
    asset = factory.asset(tenant)
    factory.finding(asset=asset, severity="high", title='=HYPERLINK("http://x.example.test")')
    factory.finding(asset=asset, severity="high", title='a, "quoted"\nvalue')
    factory.finding(asset=asset, severity="low")
    factory.finding(asset=factory.asset(), severity="high")
    report = factory.report(tenant, kind="csv", filters={"severity": ["high"]})
    with override_settings(reports_dir=tmp_path):
        path = service.generate_report(factory.session, report.id)
    assert path == str(tmp_path / str(tenant.id) / f"report-{report.id}.csv")
    rows = list(csv.reader(io.StringIO(Path(path).read_text(), newline="")))
    assert [r[7] for r in rows] == ["title", '\'=HYPERLINK("http://x.example.test")',
                                    'a, "quoted"\nvalue']
    assert rows[1][1] == asset.hostname
    assert (report.status, report.path) == ("ready", path)


def test_html_and_json_reports(factory, tmp_path):
    tenant = factory.tenant(name="Acme <Shipping>")
    factory.finding(asset=factory.asset(tenant), title="<script>alert(1)</script>")
    html_report = factory.report(tenant, kind="html")
    json_report = factory.report(tenant, kind="json")
    with override_settings(reports_dir=tmp_path):
        page = Path(service.generate_report(factory.session, html_report.id)).read_text()
        doc = json.loads(Path(service.generate_report(factory.session, json_report.id)).read_text())
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page and "<script>" not in page
    assert "Acme &lt;Shipping&gt;" in page
    assert doc["count"] == 1 and doc["findings"][0]["title"] == "<script>alert(1)</script>"


def test_legacy_csv_still_writes_old_layout():
    fh = io.StringIO()
    row = {"hostname": "shop.acme-shipping.example.test", "plugin": "tls_config",
           "rule_id": "cert-expired", "severity": "high", "status": "open", "title": "-1+1",
           "location": "tls:443", "first_seen_at": None, "last_seen_at": None}
    with pytest.deprecated_call():
        assert write_findings_csv([row], fh, tenant_slug="acme-shipping") == 1
    lines = fh.getvalue().splitlines()
    assert lines[0].startswith("tenant,host,check")
    assert lines[1] == ("acme-shipping,shop.acme-shipping.example.test,tls_config,cert-expired,"
                        "HIGH,open,'-1+1,tls:443,,")

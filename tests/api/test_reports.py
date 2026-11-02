import pytest

from app.core.config import override_settings


@pytest.fixture
def reports_dir(tmp_path):
    with override_settings(reports_dir=tmp_path):
        yield tmp_path


def test_request_report_stores_filters(client, acme, northpeak, factory):
    body = {"kind": "csv", "filters": {"severity": ["high"], "since": "2026-10-01T00:00:00Z"}}
    response = client.post("/v1/reports", headers=acme.headers, json=body)
    assert response.status_code == 201
    report = response.json()
    assert (report["status"], report["kind"]) == ("pending", "csv")
    assert report["filters"] == {"severity": ["high"], "since": "2026-10-01T00:00:00Z"}
    foreign = {"kind": "json", "filters": {"asset_id": factory.asset(northpeak.tenant).id}}
    assert client.post("/v1/reports", headers=acme.headers, json=foreign).status_code == 404


def test_download_only_when_ready(client, acme, factory, db_session, reports_dir):
    report = factory.report(acme.tenant, kind="csv")
    url = f"/v1/reports/{report.id}/download"
    pending = client.get(url, headers=acme.headers)
    assert pending.status_code == 409
    assert pending.json()["error"]["details"] == {"status": "pending"}

    path = reports_dir / str(acme.tenant.id) / f"report-{report.id}.csv"
    path.parent.mkdir()
    path.write_text("id,title\n1,missing-hsts\n")
    report.status, report.path = "ready", str(path)
    db_session.flush()
    response = client.get(url, headers=acme.headers)
    assert response.status_code == 200
    assert response.text == "id,title\n1,missing-hsts\n"
    assert response.headers["content-type"].startswith("text/csv")
    assert f'filename="report-{report.id}.csv"' in response.headers["content-disposition"]


def test_download_of_another_tenants_report_is_404(client, acme, northpeak, factory, reports_dir):
    path = reports_dir / str(northpeak.tenant.id) / "report-1.json"
    path.parent.mkdir()
    path.write_text("{}")
    theirs = factory.report(northpeak.tenant, status="ready", path=str(path))
    assert client.get(f"/v1/reports/{theirs.id}", headers=acme.headers).status_code == 404
    assert client.get(f"/v1/reports/{theirs.id}/download", headers=acme.headers).status_code == 404
    # A row pointing outside the caller's tenant directory is never served.
    mine = factory.report(acme.tenant, status="ready", path=str(path))
    assert client.get(f"/v1/reports/{mine.id}/download", headers=acme.headers).status_code == 404
    own = client.get(f"/v1/reports/{theirs.id}/download", headers=northpeak.headers)
    assert own.status_code == 200

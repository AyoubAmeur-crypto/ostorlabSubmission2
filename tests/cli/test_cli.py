import sys
import types
from datetime import timedelta

from sqlalchemy import select

from app import cli
from app.core.auth import verify_token
from app.models import ApiToken, Job, OutboxEntry, Tenant


def fake_module(monkeypatch, name, **attrs):
    """Stand in for another component's module: these tests are about argument handling."""
    monkeypatch.setitem(sys.modules, name, types.SimpleNamespace(**attrs))


def test_create_tenant_validates_and_refuses_duplicates(db_session, capsys):
    argv = ["create-tenant", "--slug", "harbor-co", "--name", "Harbor Co",
            "--timezone", "Europe/Lisbon", "--allowed-ports", "443,80,443"]
    assert cli.main(argv) == 0
    tenant = db_session.scalars(select(Tenant).where(Tenant.slug == "harbor-co")).one()
    assert (tenant.timezone, tenant.allowed_ports) == ("Europe/Lisbon", [80, 443])
    assert cli.main(argv) == 2
    bad_zone = ["create-tenant", "--slug", "x-co", "--name", "X", "--timezone", "Mars/Base"]
    assert cli.main(bad_zone) == 2
    err = capsys.readouterr().err
    assert "already exists" in err and "unknown time zone" in err


def test_issue_token_prints_the_plaintext_once(factory, db_session, capsys):
    tenant = factory.tenant(slug="acme-shipping")
    assert cli.main(["issue-token", "--tenant", "acme-shipping", "--label", "ci"]) == 0
    out = capsys.readouterr()
    plaintext = out.out.strip()
    row = db_session.scalars(select(ApiToken).where(ApiToken.tenant_id == tenant.id)).one()
    assert verify_token(plaintext, row.salt, row.token_hash) and row.label == "ci"
    assert plaintext not in out.err
    assert cli.main(["issue-token", "--tenant", "nope"]) == 2


def test_trigger_scan_resolves_names_and_calls_request_scan(factory, monkeypatch, capsys):
    calls = []
    fake_module(monkeypatch, "app.worker.scans",
                request_scan=lambda session, **kw: calls.append(kw) or types.SimpleNamespace(id=7))
    tenant = factory.tenant(slug="lumen-retail")
    asset = factory.asset(tenant, hostname="shop.lumen-retail.example.test")
    profile = factory.profile(tenant, name="nightly-storefront")
    argv = ["trigger-scan", "--tenant", "lumen-retail", "--profile", "nightly-storefront",
            "--asset", "SHOP.lumen-retail.example.test."]
    assert cli.main(argv) == 0
    assert calls == [{"tenant_id": tenant.id, "profile_id": profile.id, "asset_id": asset.id,
                      "trigger": "manual"}]
    assert "scan 7 queued" in capsys.readouterr().out
    other = factory.profile(factory.tenant())
    assert cli.main(argv[:3] + ["--profile", str(other.id), "--asset", str(asset.id)]) == 2


def test_export_findings_passes_filtered_tenant_rows_to_legacy_csv(factory, monkeypatch, tmp_path):
    seen = {}

    def write(rows, fh, tenant_slug):
        seen.update(ids=[r.id for r in rows], slug=tenant_slug)
        fh.write("tenant\n")
        return len(rows)

    fake_module(monkeypatch, "app.reporting.legacy_csv", write_findings_csv=write)
    asset = factory.asset(factory.tenant(slug="northpeak-health"))
    high = factory.finding(asset=asset, severity="high")
    factory.finding(asset=asset, severity="low")
    factory.finding(severity="high")  # another tenant
    out = tmp_path / "findings.csv"
    argv = ["export-findings", "--tenant", "northpeak-health", "--severity", "high", "-o", str(out)]
    assert cli.main(argv) == 0
    assert seen == {"ids": [high.id], "slug": "northpeak-health"}
    assert out.read_text() == "tenant\n"


def test_replay_job_is_a_dry_run_without_yes(factory, db_session, frozen_clock):
    scan = factory.scan(status="failed", error="job dead after 5 attempts")
    job = factory.job(payload={"scan_id": scan.id}, status="dead", attempts=5,
                      locked_by="worker-1", run_after=frozen_clock.now() - timedelta(hours=3))
    assert cli.main(["replay-job", str(job.id)]) == 0
    row = db_session.execute(select(Job.status, Job.attempts).where(Job.id == job.id)).one()
    assert tuple(row) == ("dead", 5)

    assert cli.main(["replay-job", str(job.id), "--yes"]) == 0
    db_session.expire_all()
    assert (job.status, job.attempts, job.locked_by) == ("queued", 0, None)
    assert job.run_after == frozen_clock.now()
    assert (scan.status, scan.error, scan.started_at) == ("queued", None, None)


def test_replay_job_refuses_finished_work(factory, capsys):
    done = factory.job(status="done")
    completed = factory.scan(status="completed")
    dead = factory.job(payload={"scan_id": completed.id}, status="dead", attempts=5)
    assert cli.main(["replay-job", str(done.id), "--yes"]) == 2
    assert cli.main(["replay-job", str(dead.id), "--yes"]) == 2
    assert cli.main(["replay-job", "999999", "--yes"]) == 2
    err = capsys.readouterr().err
    assert "only dead or failed" in err and "would run it again" in err and "not found" in err


def test_prune_deletes_only_old_delivered_and_done_rows(factory, db_session, frozen_clock):
    old = frozen_clock.now() - timedelta(days=45)
    hook = factory.webhook()
    old_row, recent = (factory.outbox(hook, status="delivered") for _ in range(2))
    old_row.delivered_at, recent.delivered_at = old, frozen_clock.now() - timedelta(days=2)
    dead_hook = factory.outbox(hook, status="dead")
    done_old, dead_old = factory.job(status="done"), factory.job(status="dead")
    done_old.finished_at = dead_old.finished_at = old
    db_session.flush()

    assert cli.main(["prune", "--days", "30", "--dry-run"]) == 0
    assert db_session.get(Job, done_old.id) is not None
    assert cli.main(["prune", "--days", "30"]) == 0
    db_session.expire_all()
    assert set(db_session.scalars(select(OutboxEntry.id))) == {recent.id, dead_hook.id}
    assert set(db_session.scalars(select(Job.id))) == {dead_old.id}

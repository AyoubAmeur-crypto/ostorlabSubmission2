import hashlib
from collections import Counter

from sqlalchemy import func, select

from app.models import ApiToken, Finding, FindingEvent, Tenant
from seed import generate


def fake_fingerprint(*parts):
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()


def build(seed=42, findings=1500):
    return generate.build(seed, findings, fingerprint=fake_fingerprint)


def test_same_seed_same_rows_and_tokens():
    first, second = build(), build()
    assert first.rows == second.rows and first.tokens == second.tokens
    assert build(seed=43).rows["findings"] != first.rows["findings"]


def test_volumes_and_distributions():
    ds = build(findings=6000)
    counts = {name: len(rows) for name, rows in ds.rows.items()}
    assert [counts[t] for t in ("tenants", "assets", "scan_profiles", "scans")] == [3, 40, 6, 120]
    findings = ds.rows["findings"]
    assert abs(len(findings) - 6000) < 60
    severity = Counter(f["severity"] for f in findings)
    assert severity["medium"] > severity["high"] > severity["critical"] > 0
    assert set(Counter(f["status"] for f in findings)) == {"new", "open", "fixed", "reopened"}
    assert len({(f["tenant_id"], f["fingerprint"]) for f in findings}) == len(findings)
    assert all(f["first_seen_at"] <= f["last_seen_at"] <= generate.ANCHOR for f in findings)
    assert all(t.startswith("tw_demo_") and len(t) == 40 for t in ds.tokens.values())


def test_main_loads_refuses_existing_data_and_resets(db_session, monkeypatch, capsys):
    monkeypatch.setattr(generate, "default_fingerprint", lambda: fake_fingerprint)
    assert generate.main(["--seed", "7", "--findings", "300"]) == 0
    assert db_session.scalar(select(func.count()).select_from(Finding)) == len(
        build(7, 300).rows["findings"])
    assert db_session.scalar(select(func.count()).select_from(FindingEvent)) > 300
    assert generate.main(["--seed", "7", "--findings", "300"]) == 1
    assert "refusing" in capsys.readouterr().err
    argv = ["--seed", "7", "--findings", "100", "--findings-scale", "2", "--reset"]
    assert generate.main(argv) == 0
    assert db_session.scalar(select(func.count()).select_from(Tenant)) == 3
    # sequences moved past the explicit ids, so ordinary inserts still work
    db_session.add(ApiToken(tenant_id=1, prefix="tw_demo_abcd", token_hash="0" * 64, salt="s"))
    db_session.flush()

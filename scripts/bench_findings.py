"""Time the findings list endpoint and the CSV exports on the seeded database.

    make bench    # or: python scripts/bench_findings.py --tenant lumen-retail --repeat 10

The API runs in-process through FastAPI's TestClient (no host port needed) with
a temporary token, deleted afterwards. For ~200k findings seed with
``python -m seed.generate --reset --findings-scale 34`` first.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run as a script from anywhere

from sqlalchemy import delete, func, select  # noqa: E402

from app.core import clock  # noqa: E402
from app.core.auth import create_api_token  # noqa: E402
from app.core.db import session_scope  # noqa: E402
from app.models import ApiToken, Finding, Tenant  # noqa: E402


def timed(fn: Callable[[], Any], repeat: int) -> tuple[list[float], Any]:
    samples, result = [], None
    for _ in range(repeat):
        start = time.perf_counter()
        result = fn()
        samples.append((time.perf_counter() - start) * 1000)
    return samples, result


def legacy_export(tenant: Tenant) -> None:
    from app import cli  # the real `export-findings` code path

    if cli.main(["export-findings", "--tenant", tenant.slug, "-o", os.devnull]) != 0:
        raise RuntimeError("export-findings failed")


def report_export(tenant: Tenant) -> int:
    from app.reporting.service import generate_report, request_report
    from app.schemas.finding import FindingFilters

    with session_scope() as session:
        report = request_report(session, tenant.id, "csv", FindingFilters())
        path = Path(generate_report(session, report.id))
        lines = sum(1 for _ in path.open(encoding="utf-8")) - 1
        path.unlink()
        session.rollback()  # leave no report row or job behind
        return lines


def main(argv: list[str] | None = None) -> int:
    from fastapi.testclient import TestClient

    from app.api.main import create_app

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tenant", help="tenant slug (default: the one with most findings)")
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args(argv)

    with session_scope() as session:
        count = func.count(Finding.id)
        query = (select(Tenant, count, func.max(Finding.last_seen_at))
                 .join(Finding, isouter=True).group_by(Tenant.id))
        if args.tenant:
            query = query.where(Tenant.slug == args.tenant)
        best = session.execute(query.order_by(count.desc())).first()
        if best is None:
            print("no tenant found: run `make seed` first", file=sys.stderr)
            return 1
        tenant, total, newest = best
        token_row, token = create_api_token(session, tenant.id, label="bench")

    # relative to the data, not the wall clock: seeded history ends at the world's "today"
    since = clock.isoformat((newest or clock.now()) - timedelta(days=7))
    cases = [
        ("list, first page", "/v1/findings?limit=50"),
        ("list, last page", f"/v1/findings?limit=50&offset={max(total - 50, 0)}"),
        ("list, high+critical open", "/v1/findings?severity=high&severity=critical&status=open"),
        ("list, since 7d, limit 500", f"/v1/findings?since={since}&limit=500"),
    ]
    results = []
    try:
        client = TestClient(create_app())
        headers = {"Authorization": f"Bearer {token}"}
        for name, url in cases:
            samples, response = timed(lambda u=url: client.get(u, headers=headers), args.repeat)
            response.raise_for_status()
            results.append((name, len(response.json()["items"]), samples))
        for name, fn in [("csv, legacy (cli)", legacy_export), ("csv, report", report_export)]:
            samples, rows = timed(lambda f=fn: f(tenant), max(1, args.repeat // 2))
            results.append((name, total if rows is None else rows, samples))
    finally:
        with session_scope() as session:
            session.execute(delete(ApiToken).where(ApiToken.id == token_row.id))

    print(f"tenant {tenant.slug}: {total} findings, {args.repeat} runs per list case\n")
    print(f"{'case':<28}{'rows':>8}{'median ms':>12}{'max ms':>10}")
    for name, rows, samples in results:
        print(f"{name:<28}{rows:>8}{statistics.median(samples):>12.1f}{max(samples):>10.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Operator CLI: ``python -m app.cli <command> --help`` (inside the stack:
``docker compose run --rm api python -m app.cli ...``).

Each command runs in one ``session_scope()``: it applies completely or not at
all. replay-job only prints what it would do unless ``--yes`` is given. Logging
is not configured on purpose: ``configure_logging()`` writes to stdout, where
``export-findings -o -`` writes CSV; warnings still reach stderr.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.auth import create_api_token
from app.core.db import session_scope
from app.core.errors import Conflict, NotFound, TidewrightError, ValidationFailed
from app.models import Asset, Finding, Job, OutboxEntry, Scan, ScanProfile, Tenant
from app.models.enums import FindingStatus, JobStatus, ScanStatus, Severity, values
from app.schemas.tenant import TenantCreate


def _tenant(session: Session, tenant_slug: str) -> Tenant:
    tenant = session.scalars(select(Tenant).where(Tenant.slug == tenant_slug)).one_or_none()
    if tenant is None:
        raise NotFound(f"tenant {tenant_slug!r} not found")
    return tenant


def _lookup(session: Session, model: Any, column: Any, tenant_id: int, value: str) -> Any:
    """Find a tenant's row by numeric id or by name/hostname (newest row wins)."""
    clause = model.id == int(value) if value.isdigit() else column == value
    row = session.scalars(
        select(model).where(model.tenant_id == tenant_id, clause).order_by(model.id.desc())
    ).first()
    if row is None:
        raise NotFound(f"{model.__tablename__.rstrip('s')} {value!r} not found for this tenant")
    return row


def create_tenant(session: Session, args: argparse.Namespace) -> int:
    data = TenantCreate(slug=args.slug, name=args.name, timezone=args.timezone,
                        allowed_ports=args.allowed_ports)
    if session.scalars(select(Tenant.id).where(Tenant.slug == data.slug)).first():
        raise Conflict(f"tenant {data.slug!r} already exists")
    tenant = Tenant(**data.model_dump())
    session.add(tenant)
    session.flush()
    print(f"tenant {tenant.id} {tenant.slug} ({tenant.timezone}, ports {tenant.allowed_ports})")
    return 0


def issue_token(session: Session, args: argparse.Namespace) -> int:
    tenant = _tenant(session, args.tenant)
    row, plaintext = create_api_token(session, tenant.id, args.label)
    print(f"token {row.id} ({row.prefix}...) for {tenant.slug}; shown once, store it now:",
          file=sys.stderr)
    print(plaintext)
    return 0


def trigger_scan(session: Session, args: argparse.Namespace) -> int:
    from app.worker.scans import request_scan

    tenant = _tenant(session, args.tenant)
    profile = _lookup(session, ScanProfile, ScanProfile.name, tenant.id, args.profile)
    asset = _lookup(session, Asset, Asset.hostname, tenant.id, args.asset.lower().rstrip("."))
    scan = request_scan(session, tenant_id=tenant.id, profile_id=profile.id, asset_id=asset.id,
                        trigger="manual")
    print(f"scan {scan.id} queued: profile {profile.name} on {asset.hostname}")
    return 0


def export_findings(session: Session, args: argparse.Namespace) -> int:
    # TODO(meilin): switch to app.reporting.csv_export (v1.6 layout) once the two
    # customers importing the old columns have migrated their scripts.
    from app.reporting.legacy_csv import write_findings_csv

    tenant = _tenant(session, args.tenant)
    query = select(Finding).where(Finding.tenant_id == tenant.id).order_by(Finding.id)
    if args.severity:
        query = query.where(Finding.severity.in_(args.severity))
    if args.status:
        query = query.where(Finding.status.in_(args.status))
    rows = session.scalars(query).all()
    if args.output == "-":
        count = write_findings_csv(rows, sys.stdout, tenant_slug=tenant.slug)
    else:
        with open(args.output, "w", newline="", encoding="utf-8") as fh:
            count = write_findings_csv(rows, fh, tenant_slug=tenant.slug)
    print(f"exported {count} findings for {tenant.slug}", file=sys.stderr)
    return 0


def replay_job(session: Session, args: argparse.Namespace) -> int:
    job = session.get(Job, args.job_id, with_for_update=True)
    if job is None:
        raise NotFound.for_resource("job", args.job_id)
    if job.status not in (JobStatus.DEAD, JobStatus.FAILED):
        raise Conflict(f"job {job.id} is {job.status}; only dead or failed jobs can be replayed")
    scan = None
    if job.kind == "scan.run":
        scan = session.get(Scan, (job.payload or {}).get("scan_id"), with_for_update=True)
        if scan is not None and scan.status in (ScanStatus.COMPLETED, ScanStatus.CANCELLED):
            raise Conflict(f"scan {scan.id} is {scan.status}; replaying would run it again")
    print(f"job {job.id} {job.kind} {job.payload}: {job.status}, "
          f"attempts {job.attempts}/{job.max_attempts}, last error: {job.last_error or '-'}")
    if not args.yes:
        print("dry run, nothing changed; re-run with --yes to requeue")
        return 0
    job.status, job.attempts, job.run_after = JobStatus.QUEUED, 0, clock.now()
    job.locked_by = job.locked_at = job.heartbeat_at = job.finished_at = None
    if scan is not None and scan.status == ScanStatus.FAILED:
        # a dead scan.run job fails its scan; the pipeline only runs queued scans
        scan.status, scan.error = ScanStatus.QUEUED, None
        scan.started_at = scan.finished_at = None
    print(f"job {job.id} requeued" + (f", scan {scan.id} back to queued" if scan else ""))
    return 0


def prune(session: Session, args: argparse.Namespace) -> int:
    if args.days < 1:
        raise ValidationFailed("--days must be at least 1")
    cutoff = clock.now() - timedelta(days=args.days)
    outbox = session.execute(delete(OutboxEntry).where(
        OutboxEntry.status == "delivered", OutboxEntry.delivered_at < cutoff))
    # dead jobs are kept: they are what replay-job works on
    jobs = session.execute(delete(Job).where(Job.status == JobStatus.DONE,
                                             Job.finished_at < cutoff))
    verb = "would delete" if args.dry_run else "deleted"
    print(f"{verb} {outbox.rowcount} delivered outbox rows and {jobs.rowcount} done jobs "
          f"finished before {clock.isoformat(cutoff)}")
    if args.dry_run:
        session.rollback()
    return 0


def _ports(text: str) -> list[int]:
    try:
        return [int(p) for p in text.split(",") if p.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError("expected a comma-separated list of ports") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Operator CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-tenant", help="create a tenant")
    p.add_argument("--slug", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--timezone", default="UTC", help="IANA zone, e.g. Europe/Amsterdam")
    p.add_argument("--allowed-ports", type=_ports, default=[80, 443], help="e.g. 80,443")
    p.set_defaults(func=create_tenant)

    p = sub.add_parser("issue-token", help="issue an API token (printed once)")
    p.add_argument("--tenant", required=True, help="tenant slug")
    p.add_argument("--label", default="cli")
    p.set_defaults(func=issue_token)

    p = sub.add_parser("trigger-scan", help="queue a manual scan")
    p.add_argument("--tenant", required=True, help="tenant slug")
    p.add_argument("--profile", required=True, help="profile id or name")
    p.add_argument("--asset", required=True, help="asset id or hostname")
    p.set_defaults(func=trigger_scan)

    p = sub.add_parser("export-findings", help="findings as CSV (legacy pre-v1.6 columns)")
    p.add_argument("--tenant", required=True, help="tenant slug")
    p.add_argument("--severity", action="append", choices=values(Severity))
    p.add_argument("--status", action="append", choices=values(FindingStatus))
    p.add_argument("-o", "--output", default="-", help="file path, '-' for stdout")
    p.set_defaults(func=export_findings)

    p = sub.add_parser("replay-job", help="requeue a dead or failed job with attempts reset")
    p.add_argument("job_id", type=int)
    p.add_argument("--yes", action="store_true", help="really requeue (default: dry run)")
    p.set_defaults(func=replay_job)

    p = sub.add_parser("prune", help="delete delivered outbox rows and done jobs")
    p.add_argument("--days", type=int, default=30, help="keep the last N days (30)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=prune)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with session_scope() as session:
            return args.func(session, args)
    except ValidationError as exc:
        for err in exc.errors():
            print(f"error: {'.'.join(map(str, err['loc']))}: {err['msg']}", file=sys.stderr)
    except TidewrightError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

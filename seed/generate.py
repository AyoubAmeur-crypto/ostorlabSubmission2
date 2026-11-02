"""Deterministic demo data.

    python -m seed.generate --seed 42 [--reset] [--findings-scale 34]   # 34 -> ~200k findings

Rows are bulk-inserted with explicit ids, not sent through ingest: it must be
fast at 200k findings and produce history (fixed, reopened, triaged findings,
dead jobs, delivered webhooks) that one fresh scan cannot. Fingerprints come
from app.ingest.fingerprint, so real scans dedupe against seeded findings. The
same seed gives the same rows and DEMO tokens; time is anchored at the world's
"today" (2026-10-14T09:00Z), not the wall clock.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.auth import TOKEN_BODY_LENGTH, TOKEN_PREFIX, hash_token, token_prefix
from app.models import Base, Tenant

ANCHOR = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
DEFAULT_FINDINGS = 6000
SCANS = 120
ALL_PLUGINS = ["http_headers", "tls_config", "open_ports", "dependency_audit", "secrets_in_config"]
ALL_EVENTS = ["finding.new", "finding.reopened", "finding.fixed", "scan.completed", "scan.failed"]

# slug, name, timezone, allowed ports, asset host names (under <slug>.example.test). Four
# hosts per tenant have recorded plugin data in fixtures/assets/ (www has it only for
# acme-shipping); scans of the others fail with plugin_data_missing.
TENANTS = [
    ("acme-shipping", "Acme Shipping", "Europe/Amsterdam", [80, 443],
     "www api tracking ftp booking portal partners edi status docs fleet customs vpn mail"),
    ("northpeak-health", "Northpeak Health", "America/Chicago", [443],
     "www portal fhir-api status legacy-intranet patient billing scheduling telehealth sso labs "
     "vpn"),
    ("lumen-retail", "Lumen Retail", "Australia/Sydney", [80, 443, 8443],
     "www shop checkout cdn inventory-api loyalty stores admin search images staging-shop "
     "pos-gateway status mail"),
]
PROFILES = {  # name, plugins, cron (tenant-local), options
    "acme-shipping": [
        ("baseline-web", ["http_headers", "tls_config"], "0 2 * * *", {"asset_tags": ["prod"]}),
        ("full-weekly", ALL_PLUGINS, "30 3 * * 0", {"timeout_s": 30}),
    ],
    "northpeak-health": [
        ("hipaa-weekly", ALL_PLUGINS, "0 1 * * 1",
         {"plugins": {"secrets_in_config": {"paths": ["config/"]}}}),
        ("deps-on-demand", ["dependency_audit", "secrets_in_config"], None, {}),
    ],
    "lumen-retail": [
        ("nightly-storefront", ["http_headers", "tls_config", "open_ports"], "15 2 * * *",
         {"asset_tags": ["prod"], "plugins": {"open_ports": {"ignore": [8080]}}}),
        ("pci-monthly", ALL_PLUGINS, "0 4 1 * *", {"timeout_s": 40}),
    ],
}
API_HOSTS = {"api", "fhir-api", "inventory-api", "edi", "pos-gateway"}
PLAIN_HOSTS = {"vpn", "mail", "ftp", "legacy-intranet"}
PCI, PHI = {"checkout", "shop", "pos-gateway", "billing"}, {"patient", "fhir-api", "labs"}

HEADER_RULES = [  # rule, header, severity, title
    ("missing-hsts", "strict-transport-security", "low", "HSTS header missing"),
    ("missing-csp", "content-security-policy", "low", "Content-Security-Policy missing"),
    ("missing-x-content-type-options", "x-content-type-options", "info", "nosniff not set"),
    ("server-version-disclosure", "server", "info", "Server header discloses version"),
    ("missing-referrer-policy", "referrer-policy", "info", "Referrer-Policy missing"),
    ("insecure-cookie-flags", "set-cookie", "low", "Cookie without Secure or HttpOnly"),
    ("permissive-cors", "access-control-allow-origin", "low", "CORS allows any origin"),
]
TLS_RULES = [
    ("cert-expiring-soon", "medium", "Certificate expires within 30 days"),
    ("cert-expired", "high", "Certificate has expired"),
    ("tls10-enabled", "medium", "TLS 1.0 still accepted"),
    ("weak-cipher-suites", "medium", "CBC or 3DES cipher suites offered"),
    ("self-signed-cert", "high", "Self-signed certificate"),
    ("missing-ocsp-stapling", "info", "OCSP stapling not enabled"),
]
RISKY_PORTS = {21: "medium", 22: "medium", 23: "high", 25: "medium", 3306: "high",
               3389: "high", 5432: "high", 6379: "high", 8080: "medium", 9200: "high"}
PACKAGES = [("npm", n, m) for n, m in [("lodash", 4), ("express", 4), ("axios", 1), ("ws", 8),
            ("jsonwebtoken", 8), ("minimist", 1), ("semver", 7), ("tar", 6), ("node-fetch", 2)]]
PACKAGES += [("pypi", n, m) for n, m in [("django", 4), ("requests", 2), ("pyyaml", 5),
             ("jinja2", 3), ("urllib3", 1), ("cryptography", 41), ("pillow", 9), ("flask", 2)]]
DEP_SEVERITY = (["low", "medium", "high", "critical"], [30, 45, 20, 5])
SECRET_RULES = [("generic-api-key", "high"), ("aws-access-key", "critical"),
                ("private-key-block", "critical"), ("db-password-in-url", "high"),
                ("slack-webhook-url", "high")]
SECRET_FILES = ["app.env", "settings.yaml", "docker-compose.override.yml", ".npmrc", "deploy.ini"]
STATUSES = (["new", "open", "fixed", "reopened"], [6, 55, 32, 7])
TRIAGE = (["none", "accepted_risk", "false_positive"], [88, 8, 4])
TRIAGE_NOTES = ["compensating control in WAF", "internal only, tracked in risk register",
                "scanner matched a test fixture", "vendor patch scheduled for next window"]
SCAN_OUTCOMES = [  # scan status, scan error, job status, weight
    ("completed", None, "done", 85),
    ("completed", "open_ports: no recorded port list for this asset", "done", 5),
    ("failed", "all plugins failed: tls_config: timed out after 20s", "done", 4),
    ("failed", "job dead after 5 attempts: OperationalError: server closed the connection "
     "unexpectedly", "dead", 4),
    ("cancelled", None, "done", 3),
]


@dataclass
class Dataset:
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # by table, FK order
    tokens: dict[str, str] = field(default_factory=dict)  # tenant slug -> DEMO plaintext

    def add(self, table_name: str, **values: Any) -> int:
        table = self.rows.setdefault(table_name, [])
        values["id"] = len(table) + 1
        table.append(values)
        return values["id"]


def default_fingerprint() -> Callable[..., str]:
    from app.ingest.fingerprint import compute_fingerprint

    return compute_fingerprint


def _days(n: float) -> timedelta:
    return timedelta(seconds=int(n * 86400))


def _candidates(rng: random.Random, host: str, ports: list[int]) -> list[tuple]:
    """(plugin, rule, location, severity, title, evidence) for the bounded plugins."""
    out = [("http_headers", r, f"header:{h}", s, f"{t} on {host}", {"header": h})
           for r, h, s, t in rng.sample(HEADER_RULES, rng.randint(1, 5))]
    tls_port = 8443 if 8443 in ports and rng.random() < 0.3 else 443
    out += [("tls_config", r, f"tls:{tls_port}", s, t, {"port": tls_port})
            for r, s, t in rng.sample(TLS_RULES, rng.randint(0, 3))]
    unexpected = [p for p in RISKY_PORTS if p not in ports]
    out += [("open_ports", "unexpected-open-port", f"port:{p}/tcp", RISKY_PORTS[p],
             f"Port {p}/tcp open but not in allowed ports", {"port": p, "allowed": ports})
            for p in rng.sample(unexpected, rng.randint(0, 3))]
    return out


def _bulk(rng: random.Random) -> tuple:
    if rng.random() < 0.9:
        eco, name, major = rng.choice(PACKAGES)
        version = f"{major}.{rng.randint(0, 30)}.{rng.randint(0, 40)}"
        advisory = f"TWSA-{rng.choice((2024, 2025, 2026))}-{rng.randint(1, 1999):04d}"
        return ("dependency_audit", advisory, f"package:{eco}:{name}@{version}",
                rng.choices(*DEP_SEVERITY)[0], f"{name} {version} is affected by {advisory}",
                {"package": name, "installed": version, "ecosystem": eco})
    rule, severity = rng.choice(SECRET_RULES)
    path, line = f"config/{rng.choice(SECRET_FILES)}", rng.randint(1, 400)
    return ("secrets_in_config", rule, f"file:{path}:{line}", severity,
            f"Possible {rule.replace('-', ' ')} in {path}", {"match": "DEMO-****", "line": line})


def build(seed: int = 42, findings: int = DEFAULT_FINDINGS,
          fingerprint: Callable[..., str] | None = None) -> Dataset:
    """Pure: the same arguments always return the same rows."""
    fingerprint = fingerprint or default_fingerprint()
    rng, ds = random.Random(seed), Dataset()
    tenants: list[dict[str, Any]] = []
    for index, (slug, name, tz, ports, hosts) in enumerate(TENANTS):
        created = datetime(2025, 11, 3, 10, tzinfo=UTC) + _days(40 * index)
        tid = ds.add("tenants", slug=slug, name=name, timezone=tz, allowed_ports=ports,
                     created_at=created)
        token = TOKEN_PREFIX + "".join(rng.choice("abcdefghijklmnopqrstuvwxyz0123456789")
                                       for _ in range(TOKEN_BODY_LENGTH))
        salt = f"{rng.getrandbits(128):032x}"
        ds.add("api_tokens", tenant_id=tid, prefix=token_prefix(token), salt=salt, label="seed",
               token_hash=hash_token(token, salt), created_at=created, revoked_at=None)
        ds.tokens[slug] = token
        hooks = [(f"http://mockhook:9000/hooks/{slug}", ALL_EVENTS if index != 1
                  else ["finding.new", "finding.reopened", "scan.failed"], True)]
        if slug == "lumen-retail":
            hooks.append((f"http://mockhook:9000/hooks/{slug}-siem", ["finding.new"], False))
        hook_rows = [(ds.add("webhooks", tenant_id=tid, url=url, events=events, enabled=on,
                             secret=f"DEMO-whsec-{slug}-{rng.getrandbits(48):012x}",
                             created_at=created + _days(2)), events, on)
                     for url, events, on in hooks]
        assets = []
        for host_name in hosts.split():
            tags = ["staging" if host_name.startswith("staging") else "prod"]
            tags += ["pci"] * (host_name in PCI) + ["phi"] * (host_name in PHI)
            kind = ("api" if host_name in API_HOSTS
                    else "host" if host_name in PLAIN_HOSTS else "web")
            asset_created = created + _days(rng.uniform(1, 20))
            archived = ANCHOR - _days(rng.uniform(40, 60)) if host_name == "images" else None
            aid = ds.add("assets", tenant_id=tid, hostname=f"{host_name}.{slug}.example.test",
                         kind=kind, tags=tags, created_at=asset_created, archived_at=archived)
            assets.append((aid, f"{host_name}.{slug}.example.test", asset_created, archived))
        profiles = [(ds.add("scan_profiles", tenant_id=tid, name=pname, plugins=plugins,
                            options=opts, schedule_cron=cron, enabled=True,
                            created_at=created + _days(3)), cron)
                    for pname, plugins, cron, opts in PROFILES[slug]]
        tenants.append({"id": tid, "slug": slug, "ports": ports, "assets": assets,
                        "profiles": profiles, "hooks": hook_rows, "actor": token_prefix(token)})

    last_scan: dict[int, tuple[int, datetime]] = {}  # asset id -> latest completed scan
    # denser towards today: customers added schedules over the summer
    for requested in sorted(ANCHOR - _days(0.1 + 90 * rng.random() ** 2) for _ in range(SCANS)):
        tenant = rng.choices(tenants, weights=[4, 3, 5])[0]
        profile_id, cron = rng.choice(tenant["profiles"])
        asset_id = rng.choice([a[0] for a in tenant["assets"] if a[3] is None])
        status, error, job_status, _ = rng.choices(SCAN_OUTCOMES, [o[3] for o in SCAN_OUTCOMES])[0]
        trigger = "schedule" if cron and rng.random() < 0.8 else rng.choice(["manual", "api"])
        started = requested + timedelta(seconds=rng.randint(1, 40))
        finished = started + timedelta(seconds=rng.randint(20, 900))
        scan_id = ds.add("scans", tenant_id=tenant["id"], profile_id=profile_id, asset_id=asset_id,
                         status=status, trigger=trigger, requested_at=requested,
                         started_at=started, finished_at=finished, error=error,
                         scheduled_slot=requested.replace(second=0, microsecond=0)
                         if trigger == "schedule" else None)
        ds.add("jobs", kind="scan.run", payload={"scan_id": scan_id}, status=job_status,
               attempts=5 if job_status == "dead" else 1, max_attempts=5, run_after=requested,
               locked_by=f"worker-{rng.randint(1, 2)}", locked_at=started, heartbeat_at=finished,
               last_error=error if job_status == "dead" else None, created_at=requested,
               finished_at=finished)
        if status == "completed":
            last_scan[asset_id] = (scan_id, finished)
        event = {"completed": "scan.completed", "failed": "scan.failed"}.get(status)
        for hook_id, events, enabled in tenant["hooks"]:
            if not enabled or event not in events:
                continue
            backlog = tenant["slug"] == "acme-shipping" and requested > ANCHOR - _days(2)
            ds.add("outbox", tenant_id=tenant["id"], webhook_id=hook_id, event=event,
                   payload={"scan_id": scan_id, "profile_id": profile_id, "asset_id": asset_id,
                            "status": status, "trigger": trigger, "error": error},
                   status="failed" if backlog else "delivered", attempts=3 if backlog else 1,
                   next_attempt_at=ANCHOR + timedelta(minutes=10) if backlog else finished,
                   last_error="HTTP 503 from receiver" if backlog else None,
                   created_at=finished, delivered_at=None if backlog else finished + _days(3e-5))

    weights = [(t, a, 0.1 if a[3] else rng.uniform(0.4, 1.6)) for t in tenants for a in t["assets"]]
    total_weight = sum(w for _, _, w in weights)
    for tenant, asset, weight in weights:
        asset_id, host = asset[0], asset[1]
        wanted = max(1, round(findings * weight / total_weight))
        items = _candidates(rng, host, tenant["ports"])[:wanted]
        keys = {(i[1], i[2]) for i in items}
        while len(items) < wanted:
            item = _bulk(rng)
            if (item[1], item[2]) not in keys:
                keys.add((item[1], item[2]))
                items.append(item)
        for item in items:
            _finding(ds, rng, tenant, asset, last_scan.get(asset_id), item, fingerprint)
    return ds


def _finding(ds: Dataset, rng: random.Random, tenant: dict[str, Any], asset: tuple,
             last: tuple[int, datetime] | None, item: tuple, fingerprint: Callable) -> None:
    asset_id, _, asset_created, archived = asset
    plugin, rule, location, severity, title, evidence = item
    status = rng.choices(*STATUSES)[0] if not archived else "fixed"
    floor = asset_created + _days(1)
    scan_id, fixed_at = None, None
    if status == "fixed":
        end = archived or ANCHOR
        first = max(floor, end - _days(rng.uniform(20, 175)))
        last_seen = min(first + _days(rng.uniform(0, 15)), end)
        fixed_at = min(last_seen + _days(rng.uniform(1, 10)), end)
    else:
        scan_id, last_seen = last or (None, ANCHOR - _days(rng.uniform(5, 60)))
        first = max(floor, last_seen - _days(rng.uniform(0.5, 7) if status == "new"
                                             else rng.uniform(2, 170)))
    triage = rng.choices(*TRIAGE)[0]
    finding_id = ds.add(
        "findings", tenant_id=tenant["id"], asset_id=asset_id, plugin=plugin, rule_id=rule,
        fingerprint=fingerprint(tenant["id"], asset_id, plugin, rule, location), title=title,
        severity=severity, status=status, triage=triage, first_seen_at=first,
        last_seen_at=last_seen, fixed_at=fixed_at, location=location, evidence=evidence,
        scan_id_last=scan_id)
    span = last_seen - first
    events = [(first, "system:ingest", "created", {"severity": severity})]
    if status == "fixed":
        events.append((fixed_at, "system:ingest", "fixed", {}))
    if status == "reopened":
        events += [(first + span * 0.4, "system:ingest", "fixed", {}),
                   (first + span * 0.7, "system:ingest", "reopened", {})]
    if triage != "none":
        events.append((first + span * 0.2, f"token:{tenant['actor']}", "triage_changed",
                       {"from": "none", "to": triage, "note": rng.choice(TRIAGE_NOTES)}))
    if rng.random() < 0.03:
        events.append((first + span * 0.5, "system:ingest", "severity_changed",
                       {"from": "medium" if severity != "medium" else "low", "to": severity}))
    for at, actor, kind, data in sorted(events, key=lambda e: e[0]):
        ds.add("finding_events", finding_id=finding_id, at=at, actor=actor, kind=kind, data=data)


def load(session: Session, ds: Dataset, chunk: int = 5000) -> None:
    for name, rows in ds.rows.items():
        table = Base.metadata.tables[name]
        for start in range(0, len(rows), chunk):
            session.execute(table.insert(), rows[start:start + chunk])
        # explicit ids were used: move the sequence past them for later inserts
        session.execute(text(f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                             f"{len(rows)})"))


def main(argv: list[str] | None = None) -> int:
    from app.core.db import session_scope

    parser = argparse.ArgumentParser(prog="python -m seed.generate",
                                     description="Load deterministic demo data.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--findings", type=int, default=DEFAULT_FINDINGS, help="base volume")
    parser.add_argument("--findings-scale", type=float, default=1.0,
                        help="multiplier on --findings (34 gives ~200k)")
    parser.add_argument("--reset", action="store_true", help="truncate every table first")
    args = parser.parse_args(argv)

    started = time.monotonic()
    ds = build(args.seed, max(1, round(args.findings * args.findings_scale)))
    with session_scope() as session:
        if session.scalar(select(func.count()).select_from(Tenant)):
            if not args.reset:
                print("refusing to seed: the database already has tenants (use --reset to "
                      "wipe it)", file=sys.stderr)
                return 1
            tables = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
            session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        load(session, ds)
    counts = ", ".join(f"{name}={len(rows)}" for name, rows in ds.rows.items())
    print(f"seeded (seed={args.seed}) in {time.monotonic() - started:.1f}s: {counts}")
    print("DEMO API tokens (local use only, not stored in plaintext):")
    for slug, token in ds.tokens.items():
        print(f"  {slug:<18} {token}")
    print("  admin              tw_demo_admin_localdev0000000000 (TW_ADMIN_TOKEN)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

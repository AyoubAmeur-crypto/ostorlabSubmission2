# Architecture

Owner: Leila Haddad. Last reviewed for v1.9 (2026-09). Decisions with a longer
history are in `docs/adr/`.

Tidewright Orchestrator runs simulated security checks ("plugins") against
assets that customers register, turns the raw results into deduplicated
findings with a lifecycle, and tells customers about changes through signed
webhooks and downloadable reports. It is a single Python code base deployed
as several processes that share one Postgres database.

## 1. Component map

| Component | Path | Owner | Process | Responsibility |
|---|---|---|---|---|
| core | `app/core/`, `app/models/`, `app/schemas/`, `migrations/`, `tests/conftest.py` | Leila Haddad | (library) | config, DB sessions, clock, logging, errors, token auth primitives, contracts, data model, schemas |
| api | `app/api/` | Tomasz Wrona | `api` | FastAPI app, auth and tenant scoping, rate limiting, REST routers |
| scheduler | `app/scheduler/` | Ama Owusu | `scheduler` | cron parsing, slot planning in tenant time zones, scheduled scan creation |
| worker | `app/worker/` | Ama Owusu | `worker` | job queue, retries, reaper, scan pipeline, scan state machine, outbox pump |
| plugins | `app/plugins/`, `fixtures/` | Kenji Arakawa | (in worker) | registry, five simulated plugins reading recorded fixtures |
| ingest | `app/ingest/` | Sofia Brandão | (in worker) | fingerprint, dedupe, severity mapping, lifecycle, audit trail, finding events |
| reporting | `app/reporting/`, `mockhook/` | Mei Lin (written by Yusuf Demir, intern, summer 2026) | (in worker) / `mockhook` | reports, webhook outbox and dispatch, local webhook receiver |
| cli, seed, bench | `app/cli.py`, `seed/`, `scripts/` | Mei Lin | one-off | operator CLI, deterministic demo data, benchmarks |
| platform | `Dockerfile`, `docker-compose.yml`, `Makefile`, `.github/` | Ravi Menon | - | images, compose stack, CI |

```
            +---------+   HTTP :8080    +-------------------+
 customer ->|   api   |---------------->|                   |
            +---------+                 |                   |
                 | request_scan()       |     Postgres      |
                 v                      |  (all state, the  |
            +-----------+  jobs table   |   job queue and   |
            | scheduler |-------------->|   the outbox)     |
            +-----------+               |                   |
                                        |                   |
            +-----------------------+   |                   |
            | worker                |<->|                   |
            |  claim job            |   +-------------------+
            |  plugins -> ingest    |
            |  reports              |   +-------+
            |  outbox -> webhooks --+-->|mockhook| (dev only)
            +-----------------------+   +-------+
                 ^
                 | rate-limit counters only
            +---------+
            |  redis  |  (used by api)
            +---------+
```

Redis holds nothing but the API rate-limit counters. Losing it resets the
windows and is harmless. Postgres is the only durable store
(see `docs/adr/0001-postgres-queue.md`).

## 2. Data flow of one scan

1. **Request.** `POST /v1/scans` (trigger `api`), `python -m app.cli trigger-scan`
   (trigger `manual`) or the scheduler (trigger `schedule`) calls
   `app.worker.scans.request_scan()`, which inserts a `scans` row
   (`queued`) and a `scan.run` job in the same transaction.
2. **Claim.** A worker claims the job (`SELECT ... FOR UPDATE SKIP LOCKED`),
   sets `locked_by`, `locked_at`, `heartbeat_at` and bumps `attempts`.
3. **Run.** The pipeline moves the scan to `running` and commits that on
   its own (so the API shows progress while plugins run), then builds an
   `AssetSnapshot`, and for each plugin of the profile calls
   `run_with_timeout()` with the plugin's options and timeout. A plugin that
   raises `PluginError` (including `PluginDataMissing` and `PluginTimeout`) is
   recorded and skipped; the others continue.
4. **Ingest.** Results of the plugins that completed go to
   `ingest_scan_results(..., completed_plugins=[...])`. Ingest fingerprints,
   deduplicates, applies the lifecycle, writes `finding_events` and calls
   `enqueue_event()` for `finding.new`, `finding.reopened` and
   `finding.fixed`.
5. **Finish.** The scan becomes `completed` (at least one plugin completed;
   `error` lists the plugins that did not) or `failed` (none did), and the
   worker enqueues `scan.completed` or `scan.failed`. Everything after the
   `running` commit (findings, events, outbox rows, the final scan status and
   the job's `done`) commits in one transaction; if it fails, none of it is
   kept and the retry starts the scan over from `running`.
6. **Deliver.** The worker loop also pumps the outbox: due rows are POSTed to
   the webhook URL with an HMAC signature, retried with backoff, and marked
   `delivered` or `dead`.

Reports follow the same path with a `report.generate` job:
`POST /v1/reports` creates a `reports` row (`pending`) and the job; the worker
calls `generate_report()`, which writes the file under `TW_REPORTS_DIR` and
marks the row `ready` in the job's transaction (it flushes, never commits). A
failed attempt leaves the row `pending` for the retry; when the job goes
`dead` the report becomes `failed` with the job's last error.

## 3. Data model

All tables are created by `migrations/versions/0001_initial_schema.py`; the
SQLAlchemy models in `app/models/` are the reference. Every timestamp is
`timestamptz`, stored in UTC. Enumerated values are `text` columns with a
CHECK constraint; the Python side uses the `StrEnum`s in
`app/models/enums.py`.

| Table | Key columns and rules |
|---|---|
| `tenants` | `slug` unique, `timezone` (IANA), `allowed_ports int[]` |
| `api_tokens` | `prefix` (first 12 chars, indexed), `token_hash` = sha256(salt + token), `salt`, `revoked_at` |
| `assets` | `hostname` normalized (lower case, no trailing dot, IDNA); unique per tenant among non-archived rows; `kind` web/api/host; `tags` JSON array of strings |
| `scan_profiles` | unique `(tenant_id, name)`; `plugins text[]`; `options` jsonb (below); `schedule_cron` nullable; `enabled` |
| `scans` | status queued/running/completed/failed/cancelled; trigger manual/schedule/api; `scheduled_slot` (UTC slot instant, schedule only) unique with `(profile_id, asset_id)` |
| `jobs` | status queued/running/done/failed/dead; `attempts`, `max_attempts`, `run_after`, `locked_by`, `locked_at`, `heartbeat_at`, `last_error`, `finished_at` |
| `findings` | `fingerprint` unique per tenant; severity info..critical; status new/open/fixed/reopened; triage none/accepted_risk/false_positive; `first_seen_at`, `last_seen_at`, `fixed_at`, `scan_id_last` |
| `finding_events` | append-only: `at`, `actor`, `kind` created/reopened/fixed/severity_changed/triage_changed, `data` |
| `webhooks` | `url`, `secret` (never returned), `events text[]`, `enabled` |
| `outbox` | one row per (event, webhook): `status` pending/delivered/failed/dead, `attempts`, `next_attempt_at`, `last_error`, `delivered_at` |
| `reports` | `kind` json/csv/html, `filters` (a serialized `FindingFilters`), status pending/running/ready/failed, `path`, `error` |

`scan_profiles.options`:

```json
{
  "timeout_s": 20,
  "asset_tags": ["prod"],
  "plugins": {"open_ports": {"ignore": [8080]}, "secrets_in_config": {"paths": ["config/"]}}
}
```

`timeout_s` defaults to `TW_PLUGIN_TIMEOUT_S`. `asset_tags` restricts which
assets a schedule covers (absent: every non-archived asset of the tenant).
`plugins.<name>` is passed verbatim as `options` to that plugin.

## 4. Contracts

These signatures are the agreement between components. Changing one is an
ADR-level decision: open the change with the owners of both sides. Unless
stated otherwise a function that takes a `session` **flushes but never
commits**; the caller that opened `session_scope()` owns the transaction.

### 4.1 core (`app/core/`)

```python
# app/core/config.py
get_settings() -> Settings                      # cached, from env
override_settings(**changes) -> ContextManager[Settings]   # tests

# app/core/db.py
session_scope() -> ContextManager[Session]      # commit / rollback / close
get_engine() -> Engine
get_sessionmaker() -> sessionmaker[Session]
ping() -> bool

# app/core/clock.py
now() -> datetime                               # aware, UTC. Always use this.
isoformat(dt) -> str                            # "2026-10-14T09:00:00Z" (wire format)
parse_iso(text) -> datetime                     # offset required
ensure_utc(dt) -> datetime                      # rejects naive values
frozen(at) -> ContextManager[FrozenClock]       # tests

# app/core/auth.py
generate_token() -> str                         # "tw_demo_" + 32 [a-z0-9]
token_prefix(token) -> str                      # first 12 chars, stored in api_tokens.prefix
hash_token(token, salt) -> str                  # sha256(salt + token) hex
verify_token(token, salt, expected_hash) -> bool   # constant time
create_api_token(session, tenant_id, label="") -> tuple[ApiToken, str]
revoke_api_token(session, row) -> None
parse_bearer(header_value) -> str               # raises Unauthorized
constant_time_equals(a, b) -> bool

# app/core/logging.py
configure_logging(service=None, *, level=None, fmt=None) -> None
get_logger(name) -> logging.Logger
bind(**fields) -> ContextManager[None]          # request_id, tenant_id, job_id ...
redact_text(text) -> str

# app/core/errors.py
TidewrightError(message, *, details=None)       # .code, .status_code, .to_dict()
ConfigError, ValidationFailed (422), Unauthorized (401), Forbidden (403),
NotFound (404), Conflict (409), InvalidTransition(entity, current, target) (409),
RateLimited(retry_after_s) (429), PluginError(plugin, message),
PluginDataMissing(plugin, message), PluginTimeout(plugin, timeout_s),
UnknownPlugin(name) (404)
```

`app/core/contracts.py` (dataclasses, no ORM or FastAPI imports):

```python
@dataclass(frozen=True, slots=True)
class AssetSnapshot:
    id: int; tenant_id: int; hostname: str; kind: str
    tags: tuple[str, ...] = (); allowed_ports: tuple[int, ...] = ()
    @classmethod
    def from_models(cls, asset: Asset, tenant: Tenant) -> AssetSnapshot

@dataclass(frozen=True, slots=True)
class RawResult:
    plugin: str; rule_id: str; title: str
    severity: str            # plugin-native label, lower case (section 4.3)
    location: str            # plugin's documented format (section 4.3)
    evidence: dict[str, Any] # JSON-serializable, secrets already redacted
    asset_id: int
    def to_dict(self) -> dict[str, Any]

@dataclass(frozen=True, slots=True)
class PluginContext:
    fixtures_root: Path
    clock: Callable[[], datetime] = app.core.clock.now
    logger: logging.Logger | logging.LoggerAdapter = logging.getLogger("app.plugins")
    scan_id: int | None = None
    def asset_dir(self, hostname: str) -> Path      # fixtures_root/assets/<hostname>
    def now(self) -> datetime
    @classmethod
    def default(cls, *, scan_id: int | None = None) -> PluginContext

class Plugin(Protocol):                              # runtime_checkable
    name: str
    version: str
    def run(self, asset: AssetSnapshot, options: Mapping[str, Any],
            context: PluginContext) -> list[RawResult]: ...

@dataclass(slots=True)
class IngestSummary:
    scan_id: int
    created: list[int]; seen: list[int]; reopened: list[int]; fixed: list[int]  # finding ids
    duplicates: int = 0
    def counts(self) -> dict[str, int]   # {"created", "seen", "reopened", "fixed", "duplicates"}
    total_results: int                   # property
```

### 4.2 plugins (`app/plugins/`)

```python
# app/plugins/registry.py
get_plugin(name: str) -> Plugin                      # raises UnknownPlugin
available() -> list[str]                             # sorted registry names
run_with_timeout(plugin: Plugin, asset: AssetSnapshot, options: Mapping[str, Any],
                 context: PluginContext, timeout_s: float) -> list[RawResult]
```

`run_with_timeout` raises `PluginTimeout` when the plugin exceeds
`timeout_s`, re-raises `PluginError` subclasses unchanged, wraps any other
exception in `PluginError`, and rejects results whose `plugin` or `asset_id`
do not match the plugin and asset it was called with.

Plugin rules: deterministic for the same fixtures and options; never open a
socket; read only under `context.fixtures_root`; raise `PluginDataMissing`
when the recorded data is absent (never return `[]` for "no data", because
`[]` means "checked, nothing found" and lets ingest mark findings fixed).

Registered names: `http_headers`, `tls_config`, `open_ports`,
`dependency_audit`, `secrets_in_config`. Recorded data lives in
`fixtures/assets/<hostname>/` plus `fixtures/advisories.json`.

### 4.3 Severity vocabularies and locations

Plugins emit their own severity labels; ingest maps them
(`app/ingest/severity.py`). Locations are part of the fingerprint and must be
stable between runs.

| Plugin | Native severities | Mapping to `Severity` | `location` format | Example `rule_id` |
|---|---|---|---|---|
| `http_headers` | `note`, `warning` | note→info, warning→low | `header:<lower-case header name>` | `missing-hsts` |
| `tls_config` | `note`, `warning`, `error` | note→info, warning→medium, error→high | `tls:<port>` | `cert-expired` |
| `open_ports` | `warning`, `error` | warning→medium, error→high | `port:<n>/<tcp\|udp>` | `unexpected-open-port` |
| `dependency_audit` | `low`, `moderate`, `high`, `critical` | low→low, moderate→medium, high→high, critical→critical | `package:<ecosystem>:<name>@<version>` | advisory id, e.g. `TWSA-2025-0141` |
| `secrets_in_config` | `high`, `critical` | high→high, critical→critical | `file:<path relative to the asset's config dir>:<line>` | `generic-api-key` |

A label that is already a bucket name (`info` ... `critical`) maps to itself
for any plugin. Anything else maps to `medium` and logs
`ingest.unknown_severity`.

### 4.4 ingest (`app/ingest/`)

```python
# app/ingest/service.py
ingest_scan_results(session: Session, scan: Scan, results: Sequence[RawResult], *,
                    completed_plugins: Sequence[str]) -> IngestSummary

# app/ingest/fingerprint.py
compute_fingerprint(tenant_id: int, asset_id: int, plugin: str, rule_id: str,
                    location: str) -> str               # sha256 hex, 64 chars

# app/ingest/severity.py
map_severity(plugin: str, native: str) -> Severity
```

Lifecycle, per finding of `scan.asset_id`:

| Before | Seen in this scan | Absent, plugin in `completed_plugins` |
|---|---|---|
| (none) | create, status `new`, event `created`, webhook `finding.new` | - |
| `new`, `open`, `reopened` | status `open`, `last_seen_at`/`scan_id_last` updated | status `fixed`, `fixed_at`, event `fixed`, webhook `finding.fixed` |
| `fixed` | status `reopened`, `fixed_at` cleared, event `reopened`, webhook `finding.reopened` | unchanged |

Findings of plugins not in `completed_plugins` are never touched. Lifecycle
never changes triage. Suppression runs after lifecycle transitions and before
audit/outbox recording: tenant-owned, unexpired rules match plugin and rule ID,
optionally asset ID and literal location prefix. The first matching rule by ID
sets `none` to `accepted_risk` with a `triage_changed` event naming the rule and
reason. Existing human triage is preserved. Matching create/reopen notifications
are withheld, including later reopens of accepted findings. Rule creation leaves
the backlog unchanged; restoration on delete/expiry is deferred.
Findings triaged `false_positive` follow the lifecycle but emit no webhooks.
A changed severity updates the row and writes a
`severity_changed` event. Event `actor` is `system:ingest`.

### 4.5 worker (`app/worker/`)

```python
# app/worker/queue.py
enqueue(session: Session, kind: JobKind | str, payload: dict[str, Any], *,
        run_after: datetime | None = None, max_attempts: int | None = None) -> Job
        # max_attempts None -> TW_JOB_MAX_ATTEMPTS (5); run_after None -> clock.now()

# app/worker/scans.py
request_scan(session: Session, *, tenant_id: int, profile_id: int, asset_id: int,
             trigger: ScanTrigger | str, scheduled_slot: datetime | None = None) -> Scan
        # inserts the scan (queued) and its scan.run job; raises NotFound if the
        # profile or asset is not the tenant's, Conflict if the asset is archived
        # or the profile is disabled
```

Job kinds and payloads: `scan.run` → `{"scan_id": int}`,
`report.generate` → `{"report_id": int}`.

Job states: `queued` → `running` → `done`; on error `failed` with
`run_after` moved by exponential backoff (`TW_RETRY_BASE_S` doubling, capped
at `TW_RETRY_MAX_S`); after `max_attempts` claims, `dead`. A job is claimable
when `status IN ('queued', 'failed') AND run_after <= now`. The reaper puts
`running` jobs whose `heartbeat_at` is older than `TW_WORKER_STALE_AFTER_S`
back to `failed` (or `dead` when out of attempts).

Scan states: `queued` → `running` → `completed` | `failed`; `queued` or
`running` → `cancelled` (API); `queued` → `failed` when the job dies before
the pipeline started the scan (reaped or out of attempts). `running` is
committed before the pipeline transaction begins. When a `scan.run` job goes
`dead`, its scan becomes `failed` and `scan.failed` is enqueued.

Cancel: `POST /v1/scans/{id}/cancel` locks the scan row (`FOR UPDATE`) and
sets `cancelled` and `finished_at`; it never touches the job. The pipeline
re-reads the status before each plugin and locks the row again before
ingesting, so a cancel either lands first (the pipeline stops, ingests nothing,
and the job still ends `done`) or waits for the pipeline's commit and then gets
`409 invalid_transition` on a finished scan. A claimed job whose scan is
already finished or cancelled is a no-op.

Replay (`python -m app.cli replay-job <id> --yes`, runbook): only `dead` or
`failed` jobs; the job goes back to `queued` with `attempts = 0`,
`run_after = now` and the lock cleared (`last_error` kept). For `scan.run`, a
`failed` scan goes back to `queued` with `error`, `started_at` and
`finished_at` cleared; a `completed` or `cancelled` scan is refused. The
worker treats the re-claimed job like a new one: `queued` → `running` → ...
(`tests/integration/test_pipeline.py` covers it). A scan still `running`
from an earlier attempt is simply started over (plugins are deterministic).

Entry point: `python -m app.worker [--once] [--worker-id ID]`. Each loop
iteration reaps stale jobs, runs at most one job, and calls
`app.reporting.webhooks.dispatch_pending()`.

### 4.6 scheduler (`app/scheduler/`)

```python
# app/scheduler/cron.py
parse_cron(expr: str) -> CronSpec          # raises ValidationFailed; m h dom mon dow,
                                           # lists, ranges, */n, a-b/n, dow 0 and 7 = Sunday
CronSpec.matches(local: datetime) -> bool  # local wall time, seconds ignored

# app/scheduler/planner.py
due_slots(cron_expr: str, tz_name: str, window_start: datetime,
          window_end: datetime) -> list[datetime]
          # aware UTC instants of the slots with window_start < slot <= window_end,
          # evaluated on the wall clock of tz_name; sorted, no duplicates

# app/scheduler/service.py
tick(session: Session, now: datetime) -> list[Scan]
          # window (now - TW_SCHEDULER_LOOKBACK_S, now]; one scan per
          # (enabled profile with schedule_cron, matching asset, slot)
```

Idempotency: `request_scan(..., scheduled_slot=slot)` inside
`session.begin_nested()`; an `IntegrityError` on
`uq_scans_profile_id_asset_id_scheduled_slot` means that slot is already
planned and is skipped. Entry point: `python -m app.scheduler [--once]`,
ticking every `TW_SCHEDULER_INTERVAL_S`.

### 4.7 reporting and webhooks (`app/reporting/`, `mockhook/`)

```python
# app/reporting/outbox.py
enqueue_event(session: Session, tenant_id: int, event: WebhookEvent | str,
              payload: dict[str, Any]) -> int
        # one outbox row per enabled webhook of the tenant subscribed to event;
        # returns the number of rows written. payload is the envelope's "data".

# app/reporting/webhooks.py
sign(secret: str, timestamp: int, body: bytes) -> str     # "t=<ts>,v1=<hex>"
verify(secret: str, header: str, body: bytes, *, now: int, tolerance_s: int = 300) -> bool
dispatch_pending(session: Session, *, limit: int = 50,
                 client: httpx.Client | None = None) -> int   # rows delivered

# app/reporting/service.py
request_report(session: Session, tenant_id: int, kind: ReportKind | str,
               filters: FindingFilters) -> Report          # row + report.generate job
generate_report(session: Session, report_id: int) -> str   # writes file, returns its path;
                                                            # flushes, never commits
```

Payload builders are shared: `FindingEventData.from_finding(f)` and
`ScanEventData.from_scan(scan, summary.counts())` in `app/schemas/webhook.py`,
dumped with `model_dump(mode="json")`.

Delivery: `POST <url>` with body `WebhookEnvelope`
(`{"id", "event", "tenant_id", "occurred_at", "data"}`) and headers
`Content-Type: application/json`, `X-Tidewright-Event`,
`X-Tidewright-Delivery` (outbox id), `X-Tidewright-Signature` where `v1` is
`hex(hmac_sha256(secret, f"{t}.".encode() + body))`. A 2xx marks the row
`delivered`; anything else (or a timeout of `TW_WEBHOOK_TIMEOUT_S`) is
retried with backoff until `TW_WEBHOOK_MAX_ATTEMPTS`, then `dead`.

Report files: `TW_REPORTS_DIR/<tenant_id>/report-<id>.<kind>`.

`mockhook`: `python -m mockhook` listens on `MOCKHOOK_PORT` (9000) and appends
one JSON line per delivery to `MOCKHOOK_LOG`. Dev webhooks point at
`http://mockhook:9000/hooks/<tenant-slug>`.

### 4.8 api (`app/api/`)

```python
app.api.main:create_app() -> FastAPI
app.api.main:app                     # uvicorn target
app.api.deps.get_session()           # FastAPI dependency wrapping session_scope()
app.api.deps.current_tenant()        # -> TenantContext(tenant: Tenant, token: ApiToken)
app.api.deps.require_admin()         # TW_ADMIN_TOKEN, for /v1/admin/*
```

- `GET /healthz` (no auth): `{"status": "ok", "db": true}`.
- Tenant API under `/v1`, bearer token: `assets`, `profiles`, `scans`
  (`POST`, list, get, `POST /v1/scans/{id}/cancel`), `findings` (list, get,
  `PATCH` triage, `GET /v1/findings/{id}/events`), `webhooks`, `reports`
  (`POST`, get, `GET /v1/reports/{id}/download`).
- Admin under `/v1/admin/tenants` (create, list, update, issue and revoke
  tokens), bearer `TW_ADMIN_TOKEN`.
- Lists take `limit` (default `TW_PAGE_LIMIT_DEFAULT`, max
  `TW_PAGE_LIMIT_MAX`) and `offset`, and return `Page[T]`
  (`items`, `total`, `limit`, `offset`).
- Errors: `TidewrightError.to_dict()` with its `status_code`; request
  validation errors use code `validation_failed` (422).
- Rate limit: `TW_RATE_LIMIT_PER_MINUTE` per tenant, fixed one-minute window
  in Redis; responses carry `X-RateLimit-Limit` and `X-RateLimit-Remaining`,
  429s carry `Retry-After`.

### 4.9 cli, seed, bench

- `python -m app.cli create-tenant | issue-token | trigger-scan | export-findings | replay-job | prune`
- `python -m seed.generate --seed 42` (deterministic; volume knobs such as `--findings`)
- `python scripts/bench_findings.py`

## 5. Conventions

**Time.** Call `app.core.clock.now()`; never `datetime.now()`,
`datetime.utcnow()` or `func.now()` in application code (server defaults
exist only for raw SQL). Store UTC, compare aware datetimes, convert to the
tenant zone only to evaluate cron or to display. Wire format is
`clock.isoformat()`.

**Tenant scoping.** Every query on a tenant-owned table filters on the
`tenant_id` of the authenticated tenant. An id that exists but belongs to
another tenant is reported as `NotFound`, never `Forbidden`, so ids cannot be
probed. Services take `tenant_id` explicitly; only the API layer knows about
tokens. Older modules (reporting, the legacy CSV export) still say
`tenant_slug` in places; new code uses `tenant_id`.

**Transactions.** Open `session_scope()` at the edge (request, job, CLI
command, scheduler tick); everything below takes `session` and flushes. One
job is one transaction for its state change plus its side effects (findings,
events, outbox rows), which is what makes the outbox reliable.

**Logging.** Call `configure_logging(service)` once per process. Messages are
dotted event names (`scan.started`, `job.retry_scheduled`,
`webhook.delivered`) with fields in `extra`; common fields are `tenant_id`,
`scan_id`, `job_id`, `plugin`, `request_id`. Use `bind()` for fields that
apply to a whole request or job. Never log a full token, a webhook secret or
plugin evidence; the redaction filter is a safety net, not a licence.

**Errors.** Raise `app.core.errors` types from services. The API maps them;
the worker stores `message` in `jobs.last_error` / `scans.error`. Do not
leak stack traces or SQL in API responses.

**Enums.** Compare with the `StrEnum` members (`ScanStatus.RUNNING`); write
either the member or its value.

**JSON columns.** Reassign (`row.evidence = {...}`) rather than mutating in
place; plain JSONB columns do not track in-place changes.

## 6. Tests

`make test` runs pytest in the `tests` compose service against the
`tidewright_test` database and Redis db 15. `tests/conftest.py` provides:

| Fixture | Use |
|---|---|
| `engine` | session-scoped; database created if missing, schema dropped and migrated to head with Alembic |
| `db_session` | one connection and outer transaction rolled back after the test; all `session_scope()` sessions opened by the code under test share it, so code may "commit" freely |
| `committed_db`, `committed_session` | real commits on several connections (SKIP LOCKED, concurrent workers); all tables truncated afterwards; mark with `@pytest.mark.committed` |
| `frozen_clock` | `FrozenClock` at 2026-10-14T09:00:00Z with `.advance(...)` / `.set(...)` |
| `factory` | `tests.factories.Factory` bound to `db_session`: `tenant`, `token`, `asset`, `profile`, `scan`, `job`, `finding`, `finding_event`, `webhook`, `outbox`, `report` |
| `today` | the world's today as a datetime |

Layout: `tests/<component>/test_*.py` with an `__init__.py` per directory.
Component-specific fixtures go in `tests/<component>/conftest.py`. Tests
never use the network (webhook tests pass an `httpx.Client` with
`httpx.MockTransport`), write files only under `tmp_path`
(`override_settings(reports_dir=tmp_path)`), and use their own Redis keys.
`factory.finding()` uses a placeholder fingerprint; ingest tests compute the
real one with `compute_fingerprint`.

Component tests replace their neighbours (the worker tests stub plugins,
ingest and reporting in `tests/worker/conftest.py`). `tests/integration/`
runs the real chain on committed data instead: `request_scan`, the worker's
`run_one`, the plugins on `fixtures/`, ingest, the outbox and a signed
delivery through `httpx.MockTransport`, a report job, and a replayed dead job.
Keep it small; it is the test that catches contract drift between components.

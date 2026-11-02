# Tidewright Orchestrator

Backend for Tidewright's hosted scanning service. Customers register assets
(web apps, APIs, hosts), choose scan profiles, and get deduplicated findings
with a lifecycle (new, open, fixed, reopened), signed webhooks and
downloadable reports. Scans are run by **simulated** plugins that read
recorded fixtures; nothing in this repository talks to the network except the
webhook dispatcher, which in development only reaches the local `mockhook`
container.

Current version: 1.9.2 (see `CHANGELOG.md`).

## Requirements

Docker with the compose plugin. Nothing else is needed on the host; Python
3.12, Postgres 16 and Redis 7 run in containers.

## Getting started

```sh
make setup        # build images, start db/redis, migrate, seed demo data, start everything
curl -s http://127.0.0.1:8080/healthz
make test         # full test suite against a separate tidewright_test database
```

The API listens on `127.0.0.1:8080` only. Postgres and Redis are reachable
from the compose network, not from the host; use `make psql` or `make shell`.
`make setup` takes well under a minute once the base images are pulled. Run
it again on an existing stack and it stops at the seed step (the database
already has tenants): `make clean` first, or reseed with `--reset` (below).

Demo tokens printed by `make seed` are DEMO credentials (`tw_demo_...`) for
local use only. The seed is deterministic, so `SEED=42` always prints the same
three tenant tokens; the admin token is `TW_ADMIN_TOKEN` from
`docker-compose.yml`.

## Trying it out

```sh
TOKEN=tw_demo_...   # acme-shipping token printed by `make seed`
curl -s -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8080/v1/assets?limit=5"
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"profile_id": 2, "asset_id": 3}' http://127.0.0.1:8080/v1/scans
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8080/v1/scans/121
make logs SERVICE=worker                    # job.claimed ... scan.finished, webhook.delivered
tail -n 3 var/mockhook/deliveries.jsonl     # what the local webhook receiver got
```

Only the hosts that have recorded data under `fixtures/assets/` produce real
results (four per tenant, e.g. `tracking.acme-shipping.example.test`); a scan
of any other seeded asset ends `failed` with `plugin_data_missing`. The first
real scan of a seeded asset marks most of its seeded findings `fixed`: the
seed invents plausible history, it does not replay the fixtures.

The operator CLI runs in a one-off container:

```sh
docker compose run --rm api python -m app.cli --help
docker compose run --rm api python -m app.cli trigger-scan --tenant acme-shipping \
    --profile full-weekly --asset tracking.acme-shipping.example.test
docker compose run --rm api python -m app.cli export-findings --tenant lumen-retail -o var/lumen.csv
docker compose run --rm api python -m app.cli replay-job 4312          # dry run, add --yes
```

## Tests

`make test` runs the whole suite (about 300 tests, a few seconds) in the
`tests` service against `tidewright_test` and Redis db 15, so it never touches
your dev data and works while the stack is up. Pass pytest arguments with
`PYTEST_ARGS`:

```sh
make test PYTEST_ARGS="tests/ingest -x"
make test PYTEST_ARGS="-k cancel -vv"
make test PYTEST_ARGS="tests/integration"   # the end-to-end pipeline test
make lint                                   # ruff; CI runs it too
```

Tests freeze the clock at the world's "today" (2026-10-14T09:00:00Z) with the
`frozen_clock` fixture; the running stack uses the real clock. Fixtures and
conventions are described in `docs/architecture.md` section 6.

## Seed data and benchmarks

`make seed` loads 3 tenants, 40 assets, 6 profiles, 120 historical scans and
about 6,000 findings, anchored at 2026-10-14. It refuses to run on a database
that already has tenants; pass `--reset` to wipe and reload:

```sh
make seed SEED_ARGS="--reset"                       # back to the demo data
make seed SEED_ARGS="--reset --findings-scale 34"   # ~200k findings (~20 s)
make bench                                          # findings list and CSV export timings
```

`scripts/bench_findings.py --tenant lumen-retail --repeat 10` picks a tenant
and the number of runs; it uses an in-process client and a temporary token,
so it works without the host port. On a laptop the list cases take tens of
milliseconds and the CSV exports about 70 ms on the demo data and about 2 s
for the largest tenant (~77k findings) at `--findings-scale 34`.

## Make targets

| Target | What it does |
|---|---|
| `make setup` | build, start db and redis, `migrate`, `seed`, start all services |
| `make up` / `make down` | start / stop the stack |
| `make clean` | stop and delete volumes (drops dev data) |
| `make migrate` | `alembic upgrade head` on the dev database |
| `make seed` | deterministic demo data (`SEED=42`, extra flags in `SEED_ARGS`) |
| `make test` | pytest in the `tests` service (`PYTEST_ARGS="-k ingest -x"`) |
| `make lint` | ruff |
| `make logs` | follow logs (`SERVICE=worker`) |
| `make shell` / `make psql` | shell in an app container / psql on the dev database |
| `make bench` | time the findings list and CSV export on seeded data |

Set `COMPOSE_PROJECT_NAME` to run an isolated copy of the stack (CI runs
several in parallel): `COMPOSE_PROJECT_NAME=tw-pr123 make test`.

## Layout

```
app/
  core/        config, db sessions, clock, logging, errors, token auth, contracts
  models/      SQLAlchemy models (whole data model)
  schemas/     Pydantic schemas shared by API, services and CLI
  api/         FastAPI app and routers
  scheduler/   cron schedules in tenant time zones
  worker/      Postgres job queue and scan pipeline
  plugins/     simulated checks + registry
  ingest/      fingerprinting, dedupe, severity, lifecycle
  reporting/   reports and webhook outbox
mockhook/      local webhook receiver (dev only)
seed/          demo data generator
migrations/    Alembic
fixtures/      recorded asset data read by the plugins
tests/         pytest, one directory per component
docs/          architecture, API, runbook, ADRs, requests
```

## Configuration

All settings come from environment variables read in `app/core/config.py`
(`DATABASE_URL`, `REDIS_URL`, `TW_*`). Compose sets sensible development
values; see the `Settings` class for every variable and its default.

## Documentation

PR-0217 review deliverables: [decision and evidence](docs/pr/PR-0217-decision.md),
[choices](CHOICES.md), and [suppression benchmark](docs/benchmarks/README.md).
Run `make bench-suppression BENCH_ARGS="--repeat 10"` to compare the original
bundled PR matcher with the fixed implementation without resetting your data.

- [Architecture](docs/architecture.md): components, data flow, contracts
  between them (section 4), conventions, test fixtures. Start here.
- [API](docs/api.md): endpoints, errors, webhooks and their signature.
  Pagination is `limit`/`offset` (`Page[T]` in `app/schemas/common.py`), see
  CHANGELOG 1.5.0.
- [Runbook](docs/runbook.md): on-call: stuck and dead jobs, `replay-job`,
  webhook backlog, pruning, DST.
- [ADRs](docs/adr/): job queue, fingerprints, webhook outbox.
- [Requests and customer reports](docs/requests/): what product and customer
  success are asking for next.
- [CHANGELOG](CHANGELOG.md): why things are the way they are.
- `CODEOWNERS`: who to ask about which directory.

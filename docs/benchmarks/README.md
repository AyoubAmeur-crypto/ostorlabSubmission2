# Suppression matching: original PR versus fixed code

Measured 2026-11-02 in the local Docker Python 3.12 / Postgres 16 review stack.
Raw results: [suppression.json](suppression.json).

| Metric | Original PR | Fixed |
|---|---:|---:|
| Median matching time | 102.162 ms | 13.352 ms |
| Maximum of 10 measured runs | 108.189 ms | 23.947 ms |
| Candidate SQL queries per call | 1 | 1 |
| Candidate rules fetched | 3,000 | 500 |
| Predicate calls | 125,250 | 500 |
| Suppressed findings | 500 | 500 |
| New notification flags still set | 500 | 0 |
| Automatic triage audit transitions | 0 | 500 |

Median speedup: **7.65×**. Predicate checks reduced **250.5×** on this workload.

## Workload and measurement boundary

500 distinct finding rule IDs; 500 own active rules, 500 own expired rules and
2,000 other-tenant rules sharing the keys. Active own rules are inserted first,
giving the original matcher an early successful match rather than forcing it to
scan every foreign rule. Both implementations are warmed once, then timed ten
times against the same database/schema and inputs. The original implementation
is loaded directly from the bundled PR patch, without altering working app files.
Thus the timing comparison isolates matcher code; it does not measure separate
before/after index deployments.

Time includes candidate query, ORM hydration, in-memory matching, transition
audit construction and an empty flush. Findings are transient, so persisted
finding/audit/outbox writes, plugin execution, full ingest and scan latency are
excluded. Counters are measured separately from timed runs. Integration tests
verify persisted events and notification suppression independently.

Synthetic tenants/rules live in a transaction that always rolls back. No reseed,
development-data reset, token creation, webhook delivery or network scan occurs.
Postgres sequence values may advance despite rollback. Results vary by machine
and load; this is not a production SLA. Ingest row locking is outside this matcher
measurement. Export/list performance belongs to the separate CS-0391 request.

## Run it yourself

```sh
cd workspace
export COMPOSE_PROJECT_NAME=tw-suppression-review
make bench-suppression BENCH_ARGS="--repeat 10 --output var/suppression-latest.json"
cat var/suppression-latest.json
# Larger matcher workload, still rollback-only:
make bench-suppression BENCH_ARGS="--findings 1000 --foreign-rules 4000 --repeat 10"
```

The command compares original and fixed implementations each time, checks the
same suppressed IDs, and asserts the corrected candidate/audit/notification
counts. It requires the migration at head; tests independently rebuild their
own database. `make bench` remains the existing findings/export benchmark.

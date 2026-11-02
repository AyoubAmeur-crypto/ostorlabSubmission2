# Decision: merge with fixes

Customers repeatedly accept the same security findings by hand. I applied and
reviewed PR-0217, then fixed the release blockers while preserving Northpeak's
endpoint paths and field names.

Matching and deletion are tenant-scoped. Rules require plugin and rule_id,
respect expiry, preserve human triage, and record automatic changes with the
rule ID and reason. Suppression runs before transactional audit/outbox recording,
preventing new/reopened notifications. Ingest locks and refreshes existing rows
so stale objects cannot overwrite manual decisions. Migration storage now matches
the model; downgrade works and schema comparisons have no exclusions.

Recommend prospective rules: creation leaves the backlog unchanged pending
Nadia's decision. Automatic restoration after expiry/deletion, finding-list rule
display and tag matching remain explicitly deferred. I avoided unrelated export,
SLA and queue work, caching, dependencies and a generalized rule engine.

One tenant/key index and candidate buckets replace repeated scans across all
rules. On 500 synthetic findings, matching improved from 102.162 to 13.352 ms
median (7.65×); predicate checks fell from 125,250 to 500. This is matcher timing,
not total scan latency.

Verification: original suite 314 passed/1 skipped; new requirement tests initially
16 failed/4 passed. Final suite: 341 passed, no skips; Ruff, real pipeline,
rollback and migration round-trip checks pass. Benchmark data always rolls back.

AI output rejected: the AI-assisted PR's “fully tested” and “tenant-safe” claims.
Failing cross-tenant, expiry, audit and notification tests disproved them.

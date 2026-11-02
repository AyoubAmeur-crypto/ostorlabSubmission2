# Decision for Nadia: merge with fixes

The supplied PR should not merge unchanged. The reviewed workspace contains the
fixes below; all 341 tests pass, with no skips, and Ruff passes. Recommend shipping
this version with **prospective rules**: creating a rule leaves existing findings
unchanged. Nadia must choose this product default; no backlog rollout was approved
or performed. Endpoint paths and response field names remain Northpeak-compatible.

Customers currently accept hundreds of repeated findings individually. Rules
automate that decision for matching newly created or reopened findings while
retaining tenant boundaries, human decisions and audit history.

## Evidence and resolution

The unmodified branch reported **314 passed, 1 skipped in 7.45 s**. Twenty new
requirement tests exposed **16 failures, 4 passes in 1.49 s**. The green original
suite was insufficient evidence of safety. Tests below are committed regressions;
the supplied patch remains unchanged as the original review artifact.

| Issue in original PR | Reproduction | Resolution |
|---|---|---|
| Another tenant's rule suppresses a finding | ingest `test_other_tenant_cannot_suppress` | Tenant/key filtering in SQL; ownership checked again in matching. |
| Another tenant can DELETE a rule | API `test_cannot_delete_another_tenants_rule` | Reuse `get_owned`, return 404 and retain victim's rule. |
| Missing/blank rule ID silences an entire plugin | API `test_rule_id_must_be_specific`, `test_rule_id_cannot_be_omitted` | Require nonblank plugin + rule ID; legacy null rule IDs never match. |
| Expired rules still suppress, including at exact expiry | ingest `test_expiry_boundary`, previously skipped expiry test | Filter using aware application clock; expiry must be strictly future. |
| Reopening overwrites manual false-positive triage | ingest `test_human_triage_survives_reopen` | Set triage only from `none`; preserve both human triage values. |
| Notifications queued before suppression | ingest `test_suppression_prevents_new_and_reopened_webhooks` | Suppress transition webhook before `_record` writes outbox. |
| Automatic triage has no audit event | ingest `test_suppression_has_auditable_provenance` | Append `triage_changed` with rule ID, reason snapshot, actor and time. |
| A valid 2000-character reason raises a database error | API `test_reason_accepts_documented_maximum` | Migration now uses Text, matching model; remove schema-comparison exclusion. |
| Migration downgrade does nothing | Exact reproduction below; final `test_suppression_migration_round_trip` | Drop table on schema downgrade; re-upgrade and compare all metadata. |
| Creating a rule changes the undecided backlog and overwrites human decisions | API `test_creation_leaves_backlog_unchanged` | Remove backlog mutation. This is a product recommendation, not a settled requirement. |

One further regression, `test_ingest_refreshes_triage_before_reopening_a_cached_finding`,
failed after the first fixes: a stale session object overwrote an external triage
change. Ingest now refreshes and locks existing findings in ID order, serializing
with manual triage. It passes in the final suite.

Two original tests deliberately changed: plugin-wide suppression now expects no
match, and create-time backlog mutation now expects no changes. Both reflect the
review thread. The expiry test was enabled, not removed. Fingerprinting, location
normalization, scan summaries and fixed-event semantics retain their behavior.

## Actual pipeline and database checks

`tests/integration/test_pipeline.py` exercises real API rule creation → worker →
plugins → ingest → persisted audit/outbox: five unsuppressed finding notifications
plus scan.completed, and no notification for the suppressed finding. An injected
failure after ingest verifies findings, audit and outbox all roll back together.
Additional tests cover overlapping rules and subsequent reopens of accepted
findings. Matching rules suppress new/reopened notifications even when triage is
already accepted; they never replace an existing triage decision or invent an
extra automatic triage event.

Migration checks now compare the complete schema without exceptions and perform
downgrade/re-upgrade inside a rolled-back Postgres transaction. The isolated dev
database was also migrated down/up with no existing suppression rules, then
compared against metadata successfully. 0002 was corrected in place because this
is an unshipped PR. For an environment that already applied the original 0002 with
real rules, use a forward repair migration; do not drop those rules to upgrade.
Application-only rollback can retain the table; schema downgrade removes rules.

The original downgrade was also executed against a mock `op` loaded from the
bundled patch, then checked with
`assert module.op.drop_table.call_count == 1`. It failed with
`AssertionError: original downgrade leaves suppression_rules behind`; no database
was changed by this reproduction.

## Focused optimization and limits

One composite `(tenant_id, matcher_key)` index and one active-candidate query per
eligible batch; bucket candidates by tenant/plugin/rule before optional asset and
literal prefix checks. Avoid scanning every fetched rule for every finding.
No cache, rule engine, dependency, worker process or public API redesign.
Many rules for the **same** plugin/rule pair still require optional-match checks;
the improvement is bounded by bucket sizes, not a claim of constant-time matching.

See [benchmark results and commands](../benchmarks/README.md). The measured matcher
median improved **102.162 → 13.352 ms (7.65×)** on 500 synthetic findings. This is
not an end-to-end latency guarantee or an export-performance fix.

Deferred with product permission: restore triage after expiry/delete, rule
provenance in finding lists, and asset tags. Already accepted findings keep their
triage; expiry/delete prevents future matching, not historical acceptance.

## Reproduce

From the exercise directory:

```sh
cd workspace
export COMPOSE_PROJECT_NAME=tw-suppression-review
make test
make lint
make test PYTEST_ARGS="tests/integration tests/core/test_migrations.py -vv"
make bench-suppression BENCH_ARGS="--repeat 10 --output var/suppression-latest.json"
```

The stack used for review is isolated from the existing default stack. Its local
API is healthy at `http://127.0.0.1:18080/healthz`; the local override is
`var/review-compose.yml`. Tests and benchmarks use one-off containers and need no
host API port. The baseline patch is already applied; do not apply it again.

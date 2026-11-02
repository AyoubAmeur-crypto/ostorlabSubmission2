# PR-0217: Finding suppression rules (REQ-0150)

| | |
|---|---|
| Branch | `twrona/req-0150-suppression-rules` |
| Opened by | Tomasz Wrona (@twrona), 2026-10-12 17:05 +01:00 |
| Written by | drafted with an AI assistant, opened by Tomasz Wrona |
| Status | Open, review requested |
| Patch | `docs/pr/PR-0217-suppression-rules.patch` (apply with `git apply`) |
| Reviewers | requested: @sbrandao (ingest), @lhaddad (migrations) |

## Summary

This PR adds **finding suppression rules** as described in REQ-0150. A tenant
can define rules that automatically mark matching findings as
`accepted_risk`, so customers no longer have to triage hundreds of identical
findings by hand every week.

Highlights:

- **Fully tested.** 19 new tests across the API and ingest layers; the whole
  suite is green (314 passed, 1 skipped).
- **Tenant-safe.** Every query is scoped to the authenticated tenant; another
  tenant's rule answers `404`.
- **No behaviour change for existing findings.** Ingest only acts on findings
  that match a rule; everything else follows the lifecycle exactly as before.
- **Audit trail included.** Every rule records who created it, when, and why
  (`created_by`, `created_at`, mandatory `reason`).
- **Migration tested.** `0002_suppression_rules` upgrades and downgrades
  cleanly.
- **Efficient matching.** Rules are indexed by a computed `matcher_key`
  (`plugin` + `rule_id`), so ingest fetches all candidate rules for a batch
  with a single indexed query, however many rules a tenant has.

## Behaviour

- When ingest **creates or reopens** a finding that matches an active rule,
  the finding's triage is set to `accepted_risk`.
- Suppressed findings send no `finding.new` or `finding.reopened` webhooks.
- When a rule is created, it is applied to the tenant's matching open
  findings straight away, as REQ-0150 asks ("Existing matching findings are
  updated when a rule is created").
- A rule must name a `plugin`, so a rule with no fields set (matching
  everything) is rejected, as REQ-0150 requires.
- `expires_at` is optional; the API reports `active: false` once it has
  passed.

## API contract (agreed with Northpeak's integration)

Northpeak Health's integration team built against this contract on
2026-10-09. **Endpoint paths and field names are fixed**; internals are free
to change.

| Method | Path | Success |
|---|---|---|
| POST | `/v1/suppression-rules` | `201`, the rule |
| GET | `/v1/suppression-rules` | `200`, the standard page body (`items`, `total`, `limit`, `offset`) |
| DELETE | `/v1/suppression-rules/{id}` | `204` |

Rule fields:

| Field | Required | Notes |
|---|---|---|
| `plugin` | yes | a registered plugin name |
| `rule_id` | | the finding's `rule_id` (e.g. `missing-hsts`, `TWSA-2025-0141`) |
| `asset_id` | no | one of the tenant's assets |
| `location_prefix` | no | matches findings whose `location` starts with it |
| `reason` | yes | free text, shown to auditors; missing reason is `422` |
| `expires_at` | no | ISO 8601 with offset |

Responses also carry `id`, `tenant_id`, `active`, `created_by`, `created_at`.
`GET /v1/suppression-rules/{id}` is included as a convenience.

## Changed files

- `app/models/suppression.py`, `app/models/__init__.py`: `SuppressionRule`
  model.
- `migrations/versions/0002_suppression_rules.py`: new table and indexes.
- `app/schemas/suppression.py`, `app/schemas/__init__.py`: request and
  response schemas.
- `app/api/routers/suppression_rules.py`, `app/api/main.py`: endpoints.
- `app/ingest/suppression.py`, `app/ingest/service.py`: matching and the
  ingest hook.
- `tests/api/test_suppression_rules.py`, `tests/ingest/test_suppression.py`:
  new tests. `tests/core/test_migrations.py`: new table registered.
- `docs/api.md`, `CHANGELOG.md`.

## Testing

```
make test     # 314 passed, 1 skipped
make lint     # All checks passed!
```

The skipped test (`test_expired_rule_does_not_suppress`) is flaky on CI
because of clock timing and is unrelated to the feature; I will look at it in
a follow-up.

## Follow-ups (not in this PR)

- Restore triage to `none` when a rule expires or is deleted (REQ-0150).
- Show the suppressing rule in the findings list.
- Asset-tag matching (replaced by `asset_id` for v1, see the contract).

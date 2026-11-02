# Tidewright Orchestrator API (v1)

Owner: Tomasz Wrona. Written for v1.2 (2026-01); the webhooks (v1.4) and
reports (v1.6) sections were added later. Base URL in development:
`http://127.0.0.1:8080`. All bodies are JSON, all timestamps are ISO 8601 in
UTC with a `Z` suffix (`2026-10-14T09:00:00Z`).

## Authentication

Tenant endpoints under `/v1` take a tenant API token:

```
Authorization: Bearer tw_demo_4k2m9x0q...
```

Tokens are issued by an operator (`POST /v1/admin/tenants/{id}/tokens` or
`python -m app.cli issue-token`) and shown once. Only a salted hash is stored,
so a lost token cannot be recovered; issue a new one and revoke the old one.
A missing, malformed, unknown or revoked token gives `401`.

Admin endpoints under `/v1/admin` take the operator token configured in
`TW_ADMIN_TOKEN` instead.

Every resource belongs to the token's tenant. An id that belongs to another
tenant answers `404`, exactly like an id that does not exist.

## Errors

Every non-2xx response has the same body, and every response carries an
`X-Request-ID` header (yours, if you sent a well-formed one):

```json
{"error": {"code": "not_found", "message": "scan 912 not found", "details": {"resource": "scan"}}}
```

| Status | `code` | When |
|---|---|---|
| 401 | `unauthorized` | bad or missing token |
| 404 | `not_found` | no such resource for this tenant |
| 409 | `conflict`, `invalid_transition` | duplicate, or a state change that is not allowed |
| 422 | `validation_failed` | invalid body or query parameters (`details.errors` lists them) |
| 429 | `rate_limited` | see below |

## Rate limits

Each tenant gets `TW_RATE_LIMIT_PER_MINUTE` requests (120 by default) per
fixed one-minute window. Admin endpoints and `/healthz` are not limited. Responses carry `X-RateLimit-Limit` and
`X-RateLimit-Remaining`; a `429` also carries `Retry-After` in seconds.

## Pagination

List endpoints use `limit` (default 50, max 500) and `offset` (default 0),
since v1.5. The old `page`/`per_page` parameters were removed in v1.6:

```
GET /v1/findings?offset=200&limit=100
```

```json
{"items": [...], "limit": 100, "offset": 200, "total": 1287}
```

Items are returned in a stable order. Findings sort by last_seen_at descending,
then id descending; concurrent changes can shift offset pagination boundaries.

## Health

| Method | Path | Notes |
|---|---|---|
| GET | `/healthz` | no auth; `{"status": "ok", "db": true}`; `503` with `"status": "degraded"` when the database is unreachable |

## Admin: tenants

| Method | Path | Body / result |
|---|---|---|
| POST | `/v1/admin/tenants` | `{"slug", "name", "timezone", "allowed_ports"}` → tenant, `201` |
| GET | `/v1/admin/tenants` | list of tenants |
| PATCH | `/v1/admin/tenants/{id}` | any of `name`, `timezone`, `allowed_ports` → tenant, `200` |
| POST | `/v1/admin/tenants/{id}/tokens` | `{"label"}` → token row plus `token` (plaintext, once), `201` |
| DELETE | `/v1/admin/tenants/{id}/tokens/{token_id}` | revoke, `204` |

`slug` is lower-case letters, digits and dashes and cannot change.
`timezone` is an IANA name (`Europe/Amsterdam`); schedules run on that wall
clock. `allowed_ports` lists the ports the tenant expects to be open; any other
open port is an `open_ports` finding.

## Assets

| Method | Path | Notes |
|---|---|---|
| POST | `/v1/assets` | `{"hostname", "kind": "web" \| "api" \| "host", "tags": [...]}`, `201` |
| GET | `/v1/assets` | list; archived assets excluded unless `include_archived=true` |
| GET | `/v1/assets/{id}` | |
| PATCH | `/v1/assets/{id}` | `kind`, `tags`; `409` on an archived asset |
| DELETE | `/v1/assets/{id}` | archives the asset (`204`); its findings and scans are kept |

Hostnames are normalized before they are stored: lower case, trailing dot
removed, internationalized names converted to punycode. Registering a hostname
the tenant already has (after normalization) gives `409`.

## Scan profiles

| Method | Path | Notes |
|---|---|---|
| POST | `/v1/profiles` | see below, `201` |
| GET | `/v1/profiles` | list |
| GET | `/v1/profiles/{id}` | |
| PATCH | `/v1/profiles/{id}` | any field; `"enabled": false` pauses its schedule |

```json
{
  "name": "nightly-storefront",
  "plugins": ["http_headers", "tls_config", "open_ports"],
  "schedule_cron": "15 2 * * *",
  "options": {"timeout_s": 20, "asset_tags": ["prod"], "plugins": {"open_ports": {"ignore": [8080]}}}
}
```

Plugins: `http_headers`, `tls_config`, `open_ports`, `dependency_audit`,
`secrets_in_config`; an unknown name, or `options.plugins` for a plugin the
profile does not run, gives `422 validation_failed`. Every plugin accepts
`ignore_rules` (rule ids to drop); `open_ports` also takes `ignore` (port
numbers), `tls_config` takes `expiry_warning_days` (30) and
`secrets_in_config` takes `paths` (config path prefixes). A rule
or path you stop checking counts as no longer reported, so its open findings
become `fixed` on the next scan. A duplicate profile `name` gives `409`.
`schedule_cron` is a five-field cron expression (`minute hour day-of-month
month day-of-week`; lists, ranges and `*/n` steps; Sunday is 0 or 7) evaluated
in the tenant's time zone. Leave it `null` for a manual-only profile.
`asset_tags` limits scheduled scans to assets with one of the tags.

## Scans

| Method | Path | Notes |
|---|---|---|
| POST | `/v1/scans` | `{"profile_id", "asset_id"}` → scan in `queued`, `201` |
| GET | `/v1/scans` | filters: `status` (repeatable), `asset_id`, `profile_id` |
| GET | `/v1/scans/{id}` | |
| POST | `/v1/scans/{id}/cancel` | `queued` or `running` → `cancelled`, returns the scan, `200`; otherwise `409 invalid_transition` |

A scan moves `queued` → `running` → `completed` or `failed`. `completed`
means at least one plugin ran; plugins that could not run are listed in
`error`. `trigger` tells who asked: `api`, `schedule` or `manual` (operator
CLI). Scheduled scans carry the `scheduled_slot` they were planned for.
Creating a scan for an archived asset or a disabled profile gives `409`; a
profile or asset of another tenant gives `404`. The scan is created
synchronously (`201`, status `queued`) and run by a worker in the background.
Lists are newest first (`requested_at`). A scan cancelled while running stops
before its next plugin and records no findings.

## Findings

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/findings` | filters below; most recently seen first |
| GET | `/v1/findings/{id}` | |
| GET | `/v1/findings/{id}/events` | audit trail, oldest first |
| PATCH | `/v1/findings/{id}` | triage, see below; returns the finding, `200` |

Filters (combined with AND; repeat a parameter to match any of its values):

| Parameter | Example |
|---|---|
| `severity` | `severity=high&severity=critical` (`info`, `low`, `medium`, `high`, `critical`) |
| `status` | `status=open` (`new`, `open`, `fixed`, `reopened`) |
| `triage` | `triage=none` (`none`, `accepted_risk`, `false_positive`) |
| `asset_id` | `asset_id=12` |
| `plugin` | `plugin=dependency_audit` |
| `since` | `since=2026-10-01T00:00:00Z`: findings seen at or after that instant; the offset is required |

A finding is one problem on one asset, identified by its fingerprint. New
results of later scans update it instead of creating a copy: `new` on first
sight, `open` when seen again, `fixed` when a completed scan of the same asset
and plugin no longer reports it, `reopened` if it comes back.

Triage records the customer's decision and survives later scans:

```
PATCH /v1/findings/4411
{"triage": "accepted_risk", "note": "internal only, tracked in risk register"}
```

Each change writes a `triage_changed` event with the token prefix as `actor`.
Findings marked `false_positive` still follow the lifecycle but no longer send
webhooks.

## Suppression rules (unreleased)

| Method | Path | Notes |
|---|---|---|
| POST | `/v1/suppression-rules` | body below, `201` |
| GET | `/v1/suppression-rules` | list, oldest first, paginated like every list |
| GET | `/v1/suppression-rules/{id}` | |
| DELETE | `/v1/suppression-rules/{id}` | `204` |

```
POST /v1/suppression-rules
{"plugin": "http_headers", "rule_id": "missing-hsts", "asset_id": 31,
 "location_prefix": "header:", "reason": "HSTS is set by the CDN",
 "expires_at": "2027-01-31T00:00:00Z"}
```

`plugin`, `rule_id` and a nonblank `reason` (up to 2000 characters) are required.
Plugin must be registered; plugin-only rules return `422`. `asset_id`,
`location_prefix` and `expires_at` optionally narrow the rule. An asset must
belong to the authenticated tenant or the API returns `404`.
`location_prefix` is a literal, case-sensitive prefix, not a SQL pattern.
`expires_at` needs a UTC offset. Responses add `id`,
`tenant_id`, `active` (false once `expires_at` has passed), `created_by`
(token prefix) and `created_at`.

Rules match only the same tenant and are active when expiry is absent or
strictly later than the application clock (equality means expired).
On create/reopen, matching rules set triage to `accepted_risk` only when it
was `none`; human `accepted_risk` and `false_positive` decisions remain intact.
Each automatic change writes a `triage_changed` audit event with actor
`system:ingest`, `from`, `to`, `suppression_rule_id`, and a snapshot of `reason`.
The lowest rule ID wins when multiple rules match. Matching new/reopened
notifications are suppressed, including subsequent reopens of accepted findings;
fixed notifications retain the existing lifecycle behavior.

Creation is prospective: it leaves the backlog and already-open findings
unchanged, following the safe-default recommendation pending Nadia's decision.
Expired/deleted rules stop future matching. Previously assigned triage remains;
automatic restoration, finding-list rule provenance and tag matching are deferred.
Every operation is tenant-scoped; another tenant's rule returns `404`, including
on DELETE. List responses use `items`, `total`, `limit`, `offset`.

## Webhooks (v1.4)

| Method | Path | Notes |
|---|---|---|
| POST | `/v1/webhooks` | `{"url", "secret", "events": [...], "enabled"}`, `201` |
| GET | `/v1/webhooks` | list; `secret` is never returned |
| GET | `/v1/webhooks/{id}` | |
| PATCH | `/v1/webhooks/{id}` | any field; rotate the secret by sending a new one |
| DELETE | `/v1/webhooks/{id}` | `204` |

Events: `finding.new`, `finding.reopened`, `finding.fixed`, `scan.completed`,
`scan.failed`. Each delivery is a `POST` of:

```json
{"id": 5521, "event": "finding.new", "tenant_id": 2, "occurred_at": "2026-10-14T09:00:00Z",
 "data": {"finding_id": 4411, "asset_id": 12, "plugin": "tls_config", "rule_id": "cert-expired",
          "severity": "high", "status": "new", "location": "tls:443", "...": "..."}}
```

Headers: `X-Tidewright-Event`, `X-Tidewright-Delivery` (equal to `id`) and
`X-Tidewright-Signature: t=<unix seconds>,v1=<hex>`, where `v1` is
HMAC-SHA256 of `"<t>." + raw body` keyed with the webhook secret. Verify it
with a constant-time comparison and reject timestamps older than five minutes.
Any 2xx response acknowledges the delivery; anything else, or no answer within
5 seconds, is retried with backoff. Deliveries are at-least-once and may
arrive out of order: deduplicate on `id`.

## Reports (v1.6)

| Method | Path | Notes |
|---|---|---|
| POST | `/v1/reports` | `{"kind": "json" \| "csv" \| "html", "filters": {...}}`, `201`, status `pending` |
| GET | `/v1/reports/{id}` | poll until `status` is `ready` (or `failed`, see `error`) |
| GET | `/v1/reports/{id}/download` | the file; `409` until the report is ready, `404` if the file is gone |

`filters` takes the same fields as the findings list (`severity`, `status`
and `triage` as arrays); an `asset_id` of another tenant gives `404`. The row
is created synchronously (`201`) and the file is generated in the background,
usually within a few seconds. There is no list endpoint for reports; keep the
id.

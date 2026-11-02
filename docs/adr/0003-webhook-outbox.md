# ADR 0003: Webhooks through a transactional outbox

- Status: Accepted
- Date: 2026-03-02
- Deciders: Leila Haddad, Ama Owusu, Tomasz Wrona

## Context

Customers want to be told about new, reopened and fixed findings and about
finished scans, by an HTTP call to a URL they own. The events are produced by
ingest and the scan pipeline inside the job's transaction. Customer endpoints
are slow, flaky, sometimes down for hours, and occasionally answer 200 while
dropping the request.

Calling the endpoint directly from the pipeline would tie our job to their
availability: a timeout would fail the scan, a retry of the job would send the
event twice, and a crash after the commit but before the call would lose it.

## Decision

Write events to an `outbox` table in the same transaction as the change that
caused them, and deliver them separately.

- `enqueue_event(session, tenant_id, event, payload)` writes one `outbox` row
  per enabled webhook of the tenant that subscribes to the event. Nothing is
  sent inside the job.
- The worker loop calls `dispatch_pending()` after each job: it picks due
  rows (`pending` or `failed` with `next_attempt_at <= now`), POSTs the
  envelope and marks the row `delivered` on any 2xx.
- Other answers and timeouts (`TW_WEBHOOK_TIMEOUT_S`) are retried with
  exponential backoff. After `TW_WEBHOOK_MAX_ATTEMPTS` the row is `dead` and
  stays for an operator.
- Each request is signed: `X-Tidewright-Signature: t=<ts>,v1=<hex>` with
  `v1 = HMAC-SHA256(secret, "<ts>." + body)`. The timestamp lets receivers
  reject replays. `X-Tidewright-Delivery` carries the outbox row id.

We considered a separate dispatcher process and decided against it for now:
the worker loop is idle most of the time and one fewer process is one fewer
thing to deploy. Splitting it out later does not change the table.

## Consequences

- An event exists if and only if the change that caused it was committed.
- Delivery is at-least-once. Receivers must deduplicate on the delivery id;
  `docs/api.md` says so and the example receiver (`mockhook`) logs it.
- No ordering guarantee between events, even for one finding: a retried
  `finding.new` can arrive after the `finding.fixed` that followed it.
  Payloads carry the finding's status so receivers can reconcile.
- A slow customer endpoint delays the next job on that worker by up to the
  timeout per due row; the dispatcher handles at most 50 rows per loop.
- The outbox grows by one row per event per webhook. Delivered rows are
  pruned after 30 days (`python -m app.cli prune`); dead rows are kept.
- Secrets live in `webhooks.secret` and are never returned by the API or
  logged. Rotating means updating the webhook; there is no overlap window
  with two valid secrets yet.

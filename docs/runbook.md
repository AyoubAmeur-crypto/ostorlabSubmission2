# On-call runbook

Owner: Ravi Menon. Audience: whoever holds the pager. Commands assume the
compose stack; in production replace `docker compose run --rm api` with the
equivalent one-off task. Postgres is the only state that matters: Redis holds
rate-limit counters and can be flushed at will.

## First look

```sh
curl -s http://127.0.0.1:8080/healthz          # {"status": "ok", "db": true}
make logs SERVICE=worker                         # JSON lines; filter on "msg"
make psql
```

Useful log events: `job.claimed`, `job.retry_scheduled`, `job.dead`,
`job.reaped`, `scan.started`, `scan.completed`, `webhook.delivered`,
`webhook.retry_scheduled`, `webhook.dead`. Every line of a job carries `job_id`; scan lines carry
`scan_id` and `tenant_id`.

Queue overview:

```sql
SELECT kind, status, count(*), min(run_after) AS oldest_due
FROM jobs WHERE status <> 'done' GROUP BY 1, 2 ORDER BY 1, 2;
```

A healthy queue has almost nothing `queued` with `run_after` in the past and
nothing `running` for longer than a few minutes.

## Stuck jobs

**Symptom.** Scans stay `queued` or `running`; customers ask why last night's
scan has no results.

1. Is a worker alive? `docker compose ps worker`, then look for recent
   `job.claimed` lines. If the worker is down, start it; queued jobs are
   picked up in order of `run_after`.
2. Find running jobs with an old heartbeat:

   ```sql
   SELECT id, kind, payload, attempts, locked_by, heartbeat_at, now() - heartbeat_at AS silent
   FROM jobs WHERE status = 'running' ORDER BY heartbeat_at;
   ```

3. The **reaper** runs at the top of every worker loop. A `running` job whose
   `heartbeat_at` is older than `TW_WORKER_STALE_AFTER_S` (60 s) goes back to
   `failed` with a backoff, or to `dead` when it has used `max_attempts`. You
   normally do not need to touch these rows: wait one loop and check again.
4. Many jobs reaped in a row usually mean the worker is being killed mid-job
   (OOM, deploy) or a plugin hangs past its timeout. Check the worker's memory
   and `plugin.timeout` lines before replaying anything.

Do not set `status = 'queued'` by hand on a `running` job whose worker might
still be alive: two workers would run the same scan.

## Dead jobs and replay-job

A job is `dead` after `max_attempts` (5) failed claims. For a `scan.run` job
the scan is marked `failed` and a `scan.failed` webhook goes out.

```sql
SELECT id, kind, payload, attempts, last_error, finished_at
FROM jobs WHERE status = 'dead' ORDER BY finished_at DESC LIMIT 20;
```

Fix the cause first (the `last_error` and the worker logs around
`finished_at`), then replay:

```sh
docker compose run --rm api python -m app.cli replay-job 4312          # dry run: prints the job
docker compose run --rm api python -m app.cli replay-job 4312 --yes    # requeue
```

`replay-job` accepts only `dead` or `failed` jobs. It sets the job back to
`queued` with `attempts = 0` and `run_after = now`, clears the lock, and keeps
`last_error` for the record. For a `scan.run` job whose scan is `failed`, the
scan goes back to `queued` with its error cleared, so the customer sees it run
again. It refuses when the scan is `completed` or `cancelled`. There is no bulk
mode; replay jobs one at a time and watch the first one finish.

## Webhook backlog

**Symptom.** A customer reports missing or late webhooks.

```sql
SELECT w.tenant_id, w.url, o.status, count(*), min(o.next_attempt_at), max(o.attempts)
FROM outbox o JOIN webhooks w ON w.id = o.webhook_id
WHERE o.status IN ('pending', 'failed', 'dead') GROUP BY 1, 2, 3 ORDER BY 4 DESC;
```

- `failed` rows with rising `attempts`: the receiver is down or answering
  non-2xx. `last_error` has the status code. Retries back off up to
  `TW_WEBHOOK_MAX_ATTEMPTS` (8) before the row goes `dead`. Tell the customer
  (Hugo Lefèvre handles the contact); nothing to do on our side.
- `pending` rows piling up with `next_attempt_at` in the past: the worker is
  not pumping the outbox. The pump runs in the worker loop, so this is the
  stuck-worker case above.
- `dead` rows are not retried. Once the receiver is fixed and the customer
  wants the events, move them back explicitly and keep the list in the
  incident notes:

  ```sql
  UPDATE outbox SET status = 'pending', attempts = 0, next_attempt_at = now(), last_error = NULL
  WHERE webhook_id = 17 AND status = 'dead' AND created_at > '2026-10-12';
  ```

Receivers deduplicate on `X-Tidewright-Delivery`, so a redelivery is safe. In
development all webhooks point at `mockhook`; its deliveries are in
`var/mockhook/deliveries.jsonl`.

## Pruning

Delivered outbox rows and `done` jobs are kept for 30 days for support
questions, then deleted:

```sh
docker compose run --rm api python -m app.cli prune --days 30 --dry-run
docker compose run --rm api python -m app.cli prune --days 30
```

Dead jobs and undelivered outbox rows are never pruned; they need a decision.

## Time zones and DST

Schedules are evaluated on the tenant's wall clock (`tenants.timezone`), and
each scheduled scan records the UTC instant of its slot in
`scans.scheduled_slot`. Around a DST change a local time can happen twice
(clocks go back) or not at all (clocks go forward), so the first nights after
a transition deserve a look:

```sql
SELECT p.name, s.asset_id, s.scheduled_slot, count(*)
FROM scans s JOIN scan_profiles p ON p.id = s.profile_id
WHERE s.trigger = 'schedule' AND s.requested_at > now() - interval '3 days'
GROUP BY 1, 2, 3 ORDER BY 3;
```

Upcoming transitions for current customers: Lumen Retail (Australia/Sydney)
moved forward on 2026-10-04, Acme Shipping (Europe/Amsterdam) moves back on
2026-10-25, Northpeak Health (America/Chicago) on 2026-11-01. Scheduler logs
(`scheduler.tick`, `scheduler.scan_planned`) show what each tick planned.

## Escalation

Data or schema questions: Leila Haddad. Worker and scheduler: Ama Owusu.
Plugins and fixtures: Kenji Arakawa. Ingest and finding lifecycle: Sofia
Brandão. Exports and the CLI: Mei Lin.

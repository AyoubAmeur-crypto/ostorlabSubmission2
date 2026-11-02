# ADR 0001: Postgres as the job queue

- Status: Accepted
- Date: 2025-10-20
- Deciders: Leila Haddad, Ama Owusu, Ravi Menon

## Context

The orchestrator runs every scan in the background: the API answers at once
and a worker runs the plugins later. We need a queue with retries, a way to
notice crashed workers, and no lost or doubled scans. We are three engineers
before launch and run one small Postgres instance already. The expected volume
for the first year is a few thousand scans a day, so throughput is not the
constraint; operability is.

Options considered:

1. **Redis with RQ or a hand-rolled list.** Fast and simple, but a second
   durable store to back up and reason about, and enqueueing could not be
   part of the database transaction that creates the scan row. A crash between
   the two writes leaves a scan nobody runs, or a job for a scan that does not
   exist.
2. **Celery with RabbitMQ.** Mature, but brings a broker, a result backend and
   a framework whose retry and acknowledgement semantics we would have to
   learn and tune. Too much for our size.
3. **A `jobs` table in Postgres**, claimed with
   `SELECT ... FOR UPDATE SKIP LOCKED`.

## Decision

Use a `jobs` table in the main database (option 3).

- `enqueue()` inserts the job in the caller's transaction. Creating a scan and
  its `scan.run` job is one commit.
- A worker claims one due job (`status IN ('queued', 'failed') AND run_after <=
  now()`) with `FOR UPDATE SKIP LOCKED`, so concurrent workers never take the
  same row, sets `locked_by`, `heartbeat_at` and bumps `attempts`.
- A running job's worker refreshes `heartbeat_at` every
  `TW_WORKER_HEARTBEAT_S`. A reaper returns jobs whose heartbeat is older than
  `TW_WORKER_STALE_AFTER_S` to the queue.
- Failures are retried with exponential backoff (`run_after` moves forward)
  up to `max_attempts`, then the job is `dead` and waits for an operator.
- Redis stays out of the critical path; it is used only for API rate limits.

## Consequences

- One store to back up, restore and inspect. On-call can answer "what is
  stuck" with SQL.
- Delivery is at-least-once: a worker that dies after finishing the work but
  before marking the job `done` causes a rerun after the reaper fires. Job
  handlers must be idempotent, which the scan pipeline is because ingest
  deduplicates by fingerprint.
- Workers poll (`TW_WORKER_POLL_INTERVAL_S`, 1 s). Latency of up to a second
  is fine for scans. We did not add `LISTEN/NOTIFY`; we can if it ever matters.
- The table grows with every job. Finished jobs are pruned by the CLI
  (`prune`), not by the worker.
- Throughput is bounded by Postgres row locking. Our load estimate is two
  orders of magnitude below where this becomes a problem; revisit if a single
  tenant schedules more than about 50 scans per minute.

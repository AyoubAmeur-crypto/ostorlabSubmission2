# REQ-0153: On-call tooling for stuck and dead jobs

| | |
|---|---|
| From | Nadia Petrov (product), on behalf of Ravi Menon (on-call) |
| Date | 2026-10-12 |
| Customers | internal; affects all |
| Priority | Medium |
| Status | Open |

## Problem

Ravi's notes from the last three on-call weeks: every incident about late
scans started with someone pasting SQL from the runbook into `make psql`.
Twice the query was edited on the fly and missed rows; once a job was
requeued by hand while its worker was still alive, and the scan ran twice and
sent duplicate webhooks to Northpeak.

`python -m app.cli replay-job` helps for a single dead job, but on-call needs
to see the state of the queue first, and the tool should make the safe thing
the easy thing.

## What we want

- A read-only overview: jobs per kind and status, oldest due job, running
  jobs with heartbeat age and worker id, and the most recent dead jobs with
  their last error. Readable in a terminal; a machine-readable mode (JSON) so
  we can paste it into the incident channel.
- Inspect one job: its payload, attempts, timestamps and, for a `scan.run`
  job, the scan's tenant, asset, profile and status.
- Replay several dead jobs at once with a filter (kind, tenant, time range),
  with a dry run that lists exactly what would change and a confirmation.
- Refuse anything unsafe: a running job with a fresh heartbeat, a job whose
  scan already completed or was cancelled.
- Every change is logged with who ran it.

## Not needed now

A web dashboard, alerting, editing payloads.

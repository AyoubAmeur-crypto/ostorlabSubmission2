# CS-0388: Asset inventory sync from Northpeak's CMDB

| | |
|---|---|
| From | Hugo Lefèvre (customer success) |
| Date | 2026-10-06 |
| Customer | Northpeak Health (`northpeak-health`) |
| Priority | Medium; renewal conversation in December |
| Status | Needs API design |

## Customer report

> Our CMDB is the source of truth for what we expose. Every night we want to
> push the full list of external hostnames to Tidewright and have you add the
> new ones, update tags, and stop scanning what we decommissioned. Today our
> script calls POST /v1/assets one by one, ignores 409s, and never archives
> anything, so you are still scanning hosts we shut down in July.
> -- Northpeak infrastructure team

## What I know

- About 120 hostnames today, expected to reach 400 next year. Their job runs
  at 01:00 Chicago time, just before their weekly scan profile.
- The CMDB has its own stable id per host (`ci_id`, e.g. `CI0041877`) and
  sometimes renames a host while keeping the id.
- They want tags from the CMDB (`env:prod`, `owner:billing`) to replace ours
  on synced assets, but keep any tag their security team added by hand in our
  UI. They are not sure how to tell those apart either.
- They asked for a "dry run" that shows what would change before they let the
  job run unattended.
- Their network team rate-limits outbound calls; one request per run is ideal,
  a handful is fine.

## Asks from me

- An API shape I can show them: endpoint(s), payload, response, error cases,
  what happens to findings of assets that disappear from the list.
- What happens if the job sends an empty list by mistake. They did this once
  with another vendor and everything was deleted.
- Whether other tenants could use the same thing (Lumen has asked about a
  similar sync from their store-fleet tool).

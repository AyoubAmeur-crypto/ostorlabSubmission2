# CS-0391: Findings export and list are too slow for Lumen

| | |
|---|---|
| From | Hugo Lefèvre (customer success) |
| Date | 2026-10-09 |
| Customer | Lumen Retail (`lumen-retail`) |
| Priority | High, customer escalated |
| Status | Open |

## Customer report

> Since we onboarded the store fleet in September, the findings page in our
> dashboard takes 10+ seconds after the first few pages, and the CSV export
> times out on our side after 60 seconds. Our SOC pulls the full export every
> morning. Yesterday it failed three times in a row.
> -- Lumen Retail security operations

## What I know

- Lumen has about 200k findings now (mostly `dependency_audit` on the store
  devices), and the number grows every week.
- Their dashboard pages through `GET /v1/findings` with `limit=500` and an
  increasing `offset`. The first pages are fast; the last ones are the slow
  ones.
- The "CSV export" they mean is two things: their SOC uses
  `POST /v1/reports` with `kind=csv`, and once a month we run
  `python -m app.cli export-findings --tenant lumen-retail` for their
  auditors. Both are slow; the CLI one also used a lot of memory last time
  Mei ran it.
- Mei says you can get a comparable data set locally with
  `python -m seed.generate --reset --findings-scale 34` (about 200k findings
  across the three demo tenants) and time it with `make bench`.

## Asks

- Make both the list and the export usable at this size, and tell me what
  numbers I can promise them (for example "export of 200k findings in under
  N seconds").
- Anything that changes for API consumers (new parameters, different
  pagination) has to be backwards compatible: Lumen's dashboard and two other
  customers' scripts use the current API.

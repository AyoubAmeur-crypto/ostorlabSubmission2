# REQ-0142: SLA due dates per severity

| | |
|---|---|
| From | Nadia Petrov (product) |
| Date | 2026-09-29 |
| Customers | Acme Shipping, Northpeak Health (both asked in Q3 reviews) |
| Priority | High, planned for v1.10 |
| Status | Ready for engineering |

## Problem

Customers' security teams work to remediation SLAs: "criticals fixed in 7
days, highs in 30". Today they export findings and compute due dates in a
spreadsheet. Northpeak's auditors asked them to show, for each finding, when
it was due and whether it was late. Acme wants to be warned before a due date
passes, not after.

## What we want

1. **SLA policy per tenant**: number of days per severity. Defaults when a
   tenant has none: critical 7, high 30, medium 90, low 180, info none.
   Changeable through the admin API; tenant self-service can come later.
2. **Due date per finding**: `first_seen_at` + the policy days for its
   severity. A severity change recomputes it. Findings that are `fixed`, or
   triaged `accepted_risk` / `false_positive`, have no due date shown as
   overdue.
3. **API**: `due_at` on every finding, and a filter on the findings list:
   `overdue=true`, plus `due_before=<timestamp>` for "what is due next week".
4. **Reports**: a `due_at` column in the CSV and JSON reports, and an
   "Overdue" count in the HTML summary.
5. **Webhook**: a new event `finding.overdue`, sent once when an open finding
   passes its due date. Customers subscribe to it like any other event.

## Open questions for engineering

- Does a reopened finding restart its clock from the reopen date or keep the
  original due date? Northpeak expects the original; Acme did not say.
- What happens to existing findings when a tenant changes its policy?
- How do we detect "just became overdue" reliably without sending the event
  twice? Something has to run periodically.

## Out of scope

Business-day calendars, per-asset policies, email notifications.

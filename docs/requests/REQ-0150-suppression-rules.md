# REQ-0150: Finding suppression rules

| | |
|---|---|
| From | Nadia Petrov (product) |
| Date | 2026-10-01 |
| Customers | Lumen Retail, Acme Shipping |
| Priority | Medium |
| Status | Draft PR exists (AI-assisted), needs review |

## Problem

Triage works one finding at a time. Customers have patterns they want to
silence wholesale: Lumen's 300 staging hosts all report `missing-hsts` on
purpose; Acme accepts `server-version-disclosure` on every host behind their
CDN. Today they mark hundreds of findings `accepted_risk` by hand, and new
assets bring new copies of the same findings every week.

## What we want

- A tenant defines **suppression rules**: match on any combination of
  `plugin`, `rule_id`, asset tag and a location prefix, with a mandatory
  reason and an optional expiry date.
- Findings matching an active rule are triaged `accepted_risk`
  automatically when ingest creates or reopens them, with an audit event that
  names the rule. Existing matching findings are updated when a rule is
  created.
- Suppressed findings send no `finding.new` or `finding.reopened` webhooks.
- When a rule expires or is deleted, the findings it suppressed go back to
  triage `none`, unless a person triaged them since.
- API: create, list, get, delete rules; the findings list shows which rule
  suppressed a finding.

## Constraints

- A rule must never hide a finding of another tenant.
- A rule that matches everything (no fields set) is rejected.
- Rules are audited: who created them, when, why.

## Notes

Tomasz started a branch with an AI assistant to get a first version quickly.
It needs a careful review before we consider merging it.

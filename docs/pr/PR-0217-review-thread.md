# PR-0217 review thread

Comments on `PR-0217-suppression-rules.md`, oldest first.

---

**Kenji Arakawa (@karakawa)**, 2026-10-13 09:42 +01:00

On "a rule with no fields set is rejected": that is not the bar I agreed with
Nadia. A rule with only `plugin` silences a whole plugin for the tenant (every
`secrets_in_config` finding, every advisory from `dependency_audit`). For v1 a
rule must name `plugin` and `rule_id` at minimum; `asset_id`,
`location_prefix` and `expires_at` only narrow it. Northpeak's integration
always sends `rule_id`, so this does not touch the contract. Please return
`422` for anything broader.

---

**Tomasz Wrona (@twrona)**, 2026-10-13 18:10 +01:00

Fair. Most of this branch came out of the assistant in one evening; I read
through it and the suite is green, but it needs someone to go deeper than I
did. I am on the Northpeak onboarding call all of Wednesday and Thursday, so
whoever picks this up: the contract in the description is fixed, everything
else is yours to change.

---

**Leila Haddad (@lhaddad)**, 2026-10-14 08:20 +01:00

One architecture constraint, since REQ-0150 is loose on it: a rule must never
override triage that a person set. If someone marked a finding
`false_positive` or `accepted_risk` by hand, rules leave it alone, including
when the finding reopens. Rules may only set triage on findings whose triage is
`none`. That is how "ingest never changes triage" in `docs/architecture.md`
§4.4 still holds with this feature.

---

**Nadia Petrov (@npetrov)**, 2026-10-14 08:55 +01:00

Two product points before anyone merges:

1. REQ-0150 says existing matching findings are updated when a rule is
   created. I wrote that before talking to customers and it is **not
   decided**: Lumen wants it, Northpeak's compliance team wants to review its
   backlog first and does not want existing findings to change. Please do not
   treat that line as agreed. Tell me what you recommend and what the safe
   default is; I will decide.
2. Restoring triage when a rule expires or is deleted, showing the rule in the
   findings list, and asset-tag matching can wait for a follow-up, as Tomasz
   proposes. Everything else in REQ-0150 (constraints included) still stands.

Northpeak's integration is ready and waiting on us. I need to tell them today
whether this ships, ships with fixes, or not yet, and why.

# ADR 0002: Finding fingerprints

- Status: Accepted
- Date: 2025-11-10
- Deciders: Sofia Brandão, Leila Haddad, Kenji Arakawa

## Context

Every scan returns a list of raw results. Customers want one finding per real
problem that stays the same across scans, so that they can triage it once,
see when it was first and last seen, and get told when it is fixed or comes
back. We need a stable identity for a result across runs.

A raw result has `plugin`, `rule_id`, `title`, `severity`, `location` and
`evidence`, plus the asset it was found on. Titles and evidence change between
plugin versions and runs (timestamps, banner strings), so they cannot be part
of the identity. Severity can be re-rated by a plugin update and must not
create a new finding either.

`location` is the hard one. For several plugins it moves without the problem
changing: a secret found in `config/app.env` at line 12 moves to line 14 when
someone adds two lines above it; a header finding is the same header on every
run. Including it would turn ordinary edits into a "fixed" plus a "new"
finding, lose the customer's triage, and produce two webhooks for nothing.

## Decision

The fingerprint is

```
sha256("<tenant_id>|<asset_id>|<plugin>|<rule_id>")
```

as lower-case hex, stored in `findings.fingerprint` with a unique constraint on
`(tenant_id, fingerprint)`. `location` is **not** part of it; the latest
location is stored on the finding and updated on every sighting.

Rules for plugin authors (Kenji Arakawa reviews new plugins against this):

- `rule_id` must be stable and must not contain run-specific data.
- If one rule can legitimately fire for several distinct things on an asset,
  the plugin must encode the distinction in `rule_id` (for example one rule id
  per header).

## Consequences

- Triage and history survive edits that only move a problem around.
- Results with the same rule on the same asset in one scan collapse into one
  finding; ingest counts them as `duplicates` in the scan summary.
- Changing the fingerprint inputs later means recomputing every stored
  fingerprint in a migration, and customers will see findings close and
  reopen unless the backfill maps old rows to new ones carefully.
- The fingerprint is computed in exactly one place,
  `app/ingest/fingerprint.py`; the seed data generator imports it rather than
  re-implementing it.

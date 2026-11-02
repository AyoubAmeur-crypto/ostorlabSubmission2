"""Suppression rules applied at ingest (REQ-0150).

A finding that ingest creates or reopens and that matches a rule is triaged
``accepted_risk`` only if its triage is ``none``. Rules require ``plugin`` and
``rule_id``; ``asset_id`` and ``location_prefix`` may narrow the match.

One tenant-scoped indexed query fetches active candidates, grouped by matcher
key so findings only inspect rules for their own plugin/rule id. First matching
rule by id wins. This runs before audit and webhook recording.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence

from sqlalchemy import or_, select, tuple_
from sqlalchemy.orm import Session

from app.core import clock
from app.core.logging import get_logger
from app.ingest.lifecycle import Transition
from app.models import Finding, SuppressionRule
from app.models.enums import FindingEventKind, Triage

log = get_logger(__name__)

WILDCARD = "*"
SUPPRESSIBLE_OUTCOMES = ("created", "reopened")


def matcher_key(plugin: str, rule_id: str | None) -> str:
    return f"{plugin}\x1f{rule_id or WILDCARD}"


def rule_matches(rule: SuppressionRule, finding: Finding) -> bool:
    if rule.tenant_id != finding.tenant_id:
        return False
    if rule.plugin != finding.plugin:
        return False
    if rule.rule_id != finding.rule_id:
        return False
    if rule.asset_id is not None and rule.asset_id != finding.asset_id:
        return False
    if rule.location_prefix and not finding.location.startswith(rule.location_prefix):
        return False
    return True


def active_rules(session: Session, findings: Iterable[Finding]) -> list[SuppressionRule]:
    """Every rule whose matcher key can match one of ``findings``."""
    keys = {(f.tenant_id, matcher_key(f.plugin, f.rule_id)) for f in findings}
    if not keys:
        return []
    stmt = (
        select(SuppressionRule)
        .where(
            tuple_(SuppressionRule.tenant_id, SuppressionRule.matcher_key).in_(sorted(keys)),
            or_(SuppressionRule.expires_at.is_(None), SuppressionRule.expires_at > clock.now()),
        )
        .order_by(SuppressionRule.id)
    )
    return list(session.scalars(stmt))


def apply_rules(session: Session, transitions: Sequence[Transition]) -> list[int]:
    """Triage the created and reopened findings of ``transitions`` that match a
    rule. Returns the ids of the suppressed findings."""
    eligible = [t for t in transitions if t.outcome in SUPPRESSIBLE_OUTCOMES]
    rules: dict[tuple[int, str], list[SuppressionRule]] = defaultdict(list)
    for rule in active_rules(session, (t.finding for t in eligible)):
        rules[(rule.tenant_id, rule.matcher_key)].append(rule)
    suppressed: list[int] = []
    for transition in eligible:
        f = transition.finding
        candidates = rules[(f.tenant_id, matcher_key(f.plugin, f.rule_id))]
        rule = next((r for r in candidates if rule_matches(r, f)), None)
        if rule is None:
            continue
        # Keep notifications suppressed on subsequent reopens too, while
        # preserving any existing human or automatic triage decision.
        transition.webhook = None
        suppressed.append(f.id)
        if f.triage != Triage.NONE:
            continue
        f.triage = Triage.ACCEPTED_RISK.value
        transition.events.append((FindingEventKind.TRIAGE_CHANGED, {
            "from": Triage.NONE.value, "to": Triage.ACCEPTED_RISK.value,
            "suppression_rule_id": rule.id, "reason": rule.reason,
        }))
    session.flush()
    if suppressed:
        log.info("ingest.suppressed", extra={"count": len(suppressed)})
    return suppressed

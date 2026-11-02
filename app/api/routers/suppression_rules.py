"""Suppression rules (REQ-0150): create, list, get, delete.

A rule makes ingest triage matching findings ``accepted_risk``
(``app.ingest.suppression``). Creation leaves the existing backlog unchanged
pending the product decision in the PR review thread.
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from sqlalchemy import select

from app.api.deps import SessionDep, TenantDep, get_owned
from app.api.pagination import PageDep, paginate
from app.core.errors import ValidationFailed
from app.core.logging import get_logger
from app.ingest.suppression import matcher_key
from app.models import Asset, SuppressionRule
from app.schemas import Page, SuppressionRuleCreate, SuppressionRuleRead

router = APIRouter(tags=["suppression-rules"])
log = get_logger(__name__)


@router.post("/suppression-rules", status_code=201)
def create_suppression_rule(
    body: SuppressionRuleCreate, ctx: TenantDep, session: SessionDep
) -> SuppressionRuleRead:
    from app.plugins.registry import available

    if body.plugin not in available():
        raise ValidationFailed("unknown plugin", details={"plugin": body.plugin})
    if body.asset_id is not None:
        get_owned(session, Asset, body.asset_id, ctx.tenant_id, "asset")

    rule = SuppressionRule(
        tenant_id=ctx.tenant_id,
        plugin=body.plugin,
        rule_id=body.rule_id,
        asset_id=body.asset_id,
        location_prefix=body.location_prefix,
        reason=body.reason,
        expires_at=body.expires_at,
        matcher_key=matcher_key(body.plugin, body.rule_id),
        created_by=ctx.actor,
    )
    session.add(rule)
    session.flush()

    log.info(
        "suppression_rule.created",
        extra={"tenant_id": ctx.tenant_id, "rule_id": rule.id},
    )
    return SuppressionRuleRead.model_validate(rule)


@router.get("/suppression-rules")
def list_suppression_rules(
    ctx: TenantDep, session: SessionDep, page: PageDep
) -> Page[SuppressionRuleRead]:
    stmt = select(SuppressionRule).where(SuppressionRule.tenant_id == ctx.tenant_id)
    return paginate(session, stmt.order_by(SuppressionRule.id), page, SuppressionRuleRead)


@router.get("/suppression-rules/{rule_id}")
def get_suppression_rule(rule_id: int, ctx: TenantDep, session: SessionDep) -> SuppressionRuleRead:
    rule = get_owned(session, SuppressionRule, rule_id, ctx.tenant_id, "suppression rule")
    return SuppressionRuleRead.model_validate(rule)


@router.delete("/suppression-rules/{rule_id}", status_code=204)
def delete_suppression_rule(rule_id: int, ctx: TenantDep, session: SessionDep) -> Response:
    """Deletes the rule. Findings it already triaged keep ``accepted_risk``."""
    rule = get_owned(session, SuppressionRule, rule_id, ctx.tenant_id, "suppression rule")
    session.delete(rule)
    session.flush()
    log.info("suppression_rule.deleted", extra={"tenant_id": ctx.tenant_id, "rule_id": rule_id})
    return Response(status_code=204)

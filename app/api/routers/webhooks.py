"""Webhook endpoints of a tenant. The signing secret is write-only."""

from __future__ import annotations

from fastapi import APIRouter, Response
from sqlalchemy import select

from app.api.deps import SessionDep, TenantDep, get_owned
from app.api.pagination import PageDep, paginate
from app.core.logging import get_logger
from app.models import Webhook
from app.schemas import Page, WebhookCreate, WebhookRead, WebhookUpdate

router = APIRouter(tags=["webhooks"])
log = get_logger(__name__)


@router.post("/webhooks", status_code=201)
def create_webhook(body: WebhookCreate, ctx: TenantDep, session: SessionDep) -> WebhookRead:
    webhook = Webhook(tenant_id=ctx.tenant_id, **body.model_dump(mode="json"))
    session.add(webhook)
    session.flush()
    log.info("webhook.created", extra={"tenant_id": ctx.tenant_id, "webhook_id": webhook.id})
    return WebhookRead.model_validate(webhook)


@router.get("/webhooks")
def list_webhooks(ctx: TenantDep, session: SessionDep, page: PageDep) -> Page[WebhookRead]:
    stmt = select(Webhook).where(Webhook.tenant_id == ctx.tenant_id).order_by(Webhook.id)
    return paginate(session, stmt, page, WebhookRead)


@router.get("/webhooks/{webhook_id}")
def get_webhook(webhook_id: int, ctx: TenantDep, session: SessionDep) -> WebhookRead:
    webhook = get_owned(session, Webhook, webhook_id, ctx.tenant_id, "webhook")
    return WebhookRead.model_validate(webhook)


@router.patch("/webhooks/{webhook_id}")
def update_webhook(
    webhook_id: int, body: WebhookUpdate, ctx: TenantDep, session: SessionDep
) -> WebhookRead:
    webhook = get_owned(session, Webhook, webhook_id, ctx.tenant_id, "webhook", for_update=True)
    for field, value in body.model_dump(mode="json", exclude_none=True).items():
        setattr(webhook, field, value)
    session.flush()
    return WebhookRead.model_validate(webhook)


@router.delete("/webhooks/{webhook_id}", status_code=204)
def delete_webhook(webhook_id: int, ctx: TenantDep, session: SessionDep) -> Response:
    """Deletes the endpoint and, by cascade, its undelivered outbox rows."""
    webhook = get_owned(session, Webhook, webhook_id, ctx.tenant_id, "webhook")
    session.delete(webhook)
    session.flush()
    log.info("webhook.deleted", extra={"tenant_id": ctx.tenant_id, "webhook_id": webhook_id})
    return Response(status_code=204)

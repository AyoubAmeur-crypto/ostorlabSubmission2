"""Admin: tenants and their API tokens. Guarded by TW_ADMIN_TOKEN (see app.api.main)."""

from __future__ import annotations

from fastapi import APIRouter, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import SessionDep
from app.api.pagination import PageDep, paginate
from app.core.auth import create_api_token, revoke_api_token
from app.core.errors import Conflict, NotFound
from app.core.logging import get_logger
from app.models import ApiToken, Tenant
from app.schemas import (
    ApiTokenCreate,
    ApiTokenIssued,
    ApiTokenRead,
    Page,
    TenantCreate,
    TenantRead,
    TenantUpdate,
)

router = APIRouter(prefix="/admin/tenants", tags=["admin"])
log = get_logger(__name__)


def _tenant(session: Session, tenant_id: int) -> Tenant:
    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFound.for_resource("tenant", tenant_id)
    return tenant


@router.post("", status_code=201)
def create_tenant(body: TenantCreate, session: SessionDep) -> TenantRead:
    if session.scalar(select(Tenant.id).where(Tenant.slug == body.slug)) is not None:
        raise Conflict(f"tenant {body.slug!r} already exists", details={"slug": body.slug})
    tenant = Tenant(**body.model_dump())
    session.add(tenant)
    session.flush()
    log.info("admin.tenant_created", extra={"tenant_id": tenant.id, "slug": tenant.slug})
    return TenantRead.model_validate(tenant)


@router.get("")
def list_tenants(session: SessionDep, page: PageDep) -> Page[TenantRead]:
    return paginate(session, select(Tenant).order_by(Tenant.id), page, TenantRead)


@router.patch("/{tenant_id}")
def update_tenant(tenant_id: int, body: TenantUpdate, session: SessionDep) -> TenantRead:
    tenant = _tenant(session, tenant_id)
    for field, value in body.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(tenant, field, value)
    session.flush()
    return TenantRead.model_validate(tenant)


@router.post("/{tenant_id}/tokens", status_code=201)
def issue_token(tenant_id: int, body: ApiTokenCreate, session: SessionDep) -> ApiTokenIssued:
    tenant = _tenant(session, tenant_id)
    row, plaintext = create_api_token(session, tenant.id, body.label)
    log.info("admin.token_issued", extra={"tenant_id": tenant.id, "token_id": row.id})
    return ApiTokenIssued(token=plaintext, **ApiTokenRead.model_validate(row).model_dump())


@router.delete("/{tenant_id}/tokens/{token_id}", status_code=204)
def revoke_token(tenant_id: int, token_id: int, session: SessionDep) -> Response:
    row = session.get(ApiToken, token_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound.for_resource("token", token_id)
    revoke_api_token(session, row)
    log.info("admin.token_revoked", extra={"tenant_id": tenant_id, "token_id": row.id})
    return Response(status_code=204)

"""Request dependencies: database session, authentication and tenant scoping.

Tenant routes take ``ctx: TenantDep`` and filter every query on
``ctx.tenant_id``; another tenant's row is a 404, like a missing one, so ids
cannot be probed. :func:`get_owned` is how routers load a row by id.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated, Any, TypeVar

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import constant_time_equals, parse_bearer, token_prefix, verify_token
from app.core.config import get_settings
from app.core.db import session_scope
from app.core.errors import NotFound, Unauthorized
from app.models import ApiToken, Tenant

M = TypeVar("M")


def get_session() -> Iterator[Session]:
    """One transaction per request: commit on success, rollback on error."""
    with session_scope() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


@dataclass(frozen=True, slots=True)
class TenantContext:
    tenant: Tenant
    token: ApiToken

    @property
    def tenant_id(self) -> int:
        return self.tenant.id

    @property
    def actor(self) -> str:
        """Audit name for finding events (``token:<prefix>``); never the full token."""
        return f"token:{self.token.prefix}"


def current_tenant(
    session: SessionDep, authorization: Annotated[str | None, Header()] = None
) -> TenantContext:
    token = parse_bearer(authorization)
    candidates = session.scalars(
        select(ApiToken).where(
            ApiToken.prefix == token_prefix(token), ApiToken.revoked_at.is_(None)
        )
    ).all()
    for row in candidates:
        if verify_token(token, row.salt, row.token_hash):
            return TenantContext(tenant=row.tenant, token=row)
    raise Unauthorized("invalid or revoked token")


TenantDep = Annotated[TenantContext, Depends(current_tenant)]


def require_admin(authorization: Annotated[str | None, Header()] = None) -> None:
    """Guard for /v1/admin/*: the bearer value must equal TW_ADMIN_TOKEN."""
    scheme, _, value = (authorization or "").strip().partition(" ")
    if scheme.lower() != "bearer" or not constant_time_equals(
        value.strip(), get_settings().admin_token
    ):
        raise Unauthorized("admin token required")


def get_owned(
    session: Session,
    model: type[M],
    row_id: int,
    tenant_id: int,
    resource: str,
    *,
    for_update: bool = False,
) -> M:
    """Load ``model`` by id within the tenant, or raise NotFound."""
    entity: Any = model
    stmt = select(entity).where(entity.id == row_id, entity.tenant_id == tenant_id)
    if for_update:
        stmt = stmt.with_for_update()
    row = session.scalars(stmt).one_or_none()
    if row is None:
        raise NotFound.for_resource(resource, row_id)
    return row

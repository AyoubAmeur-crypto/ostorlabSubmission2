"""Assets: register, list, update and archive.

Hostnames are normalized before they are stored or compared (lower case, one
trailing dot removed, IDNA punycode), and a tenant has at most one live
(non-archived) asset per hostname. Archiving keeps the row, scans and findings.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import SessionDep, TenantDep, get_owned
from app.api.pagination import PageDep, paginate
from app.core import clock
from app.core.errors import Conflict, ValidationFailed
from app.core.logging import get_logger
from app.models import Asset
from app.schemas import AssetCreate, AssetRead, AssetUpdate, Page

router = APIRouter(tags=["assets"])
log = get_logger(__name__)

_LABEL_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")


def normalize_hostname(raw: str) -> str:
    """Canonical form of a hostname (stdlib ``idna`` codec: IDNA 2003), or ValidationFailed."""
    host = raw.strip().lower().removesuffix(".")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        host = ""
    if not host or len(host) > 253 or not all(_LABEL_RE.match(p) for p in host.split(".")):
        raise ValidationFailed("hostname is not a valid DNS name", details={"hostname": raw})
    return host


def _duplicate(hostname: str, asset_id: int | None) -> Conflict:
    return Conflict(
        f"an asset with hostname {hostname!r} already exists",
        details={"hostname": hostname, "asset_id": asset_id},
    )


@router.post("/assets", status_code=201)
def create_asset(body: AssetCreate, ctx: TenantDep, session: SessionDep) -> AssetRead:
    hostname = normalize_hostname(body.hostname)
    existing = session.scalar(
        select(Asset.id).where(
            Asset.tenant_id == ctx.tenant_id,
            Asset.hostname == hostname,
            Asset.archived_at.is_(None),
        )
    )
    if existing is not None:
        raise _duplicate(hostname, existing)
    asset = Asset(tenant_id=ctx.tenant_id, hostname=hostname, kind=body.kind, tags=body.tags)
    session.add(asset)
    try:
        session.flush()
    except IntegrityError:  # concurrent insert of the same hostname
        raise _duplicate(hostname, None) from None
    log.info("asset.created", extra={"tenant_id": ctx.tenant_id, "asset_id": asset.id})
    return AssetRead.model_validate(asset)


@router.get("/assets")
def list_assets(
    ctx: TenantDep,
    session: SessionDep,
    page: PageDep,
    include_archived: bool = False,
) -> Page[AssetRead]:
    stmt = select(Asset).where(Asset.tenant_id == ctx.tenant_id).order_by(Asset.id)
    if not include_archived:
        stmt = stmt.where(Asset.archived_at.is_(None))
    return paginate(session, stmt, page, AssetRead)


@router.get("/assets/{asset_id}")
def get_asset(asset_id: int, ctx: TenantDep, session: SessionDep) -> AssetRead:
    return AssetRead.model_validate(get_owned(session, Asset, asset_id, ctx.tenant_id, "asset"))


@router.patch("/assets/{asset_id}")
def update_asset(
    asset_id: int, body: AssetUpdate, ctx: TenantDep, session: SessionDep
) -> AssetRead:
    asset = get_owned(session, Asset, asset_id, ctx.tenant_id, "asset", for_update=True)
    if asset.is_archived:
        raise Conflict("archived assets cannot be changed", details={"asset_id": asset.id})
    for field, value in body.model_dump(mode="json", exclude_none=True).items():
        setattr(asset, field, value)
    session.flush()
    return AssetRead.model_validate(asset)


@router.delete("/assets/{asset_id}", status_code=204)
def archive_asset(asset_id: int, ctx: TenantDep, session: SessionDep) -> Response:
    asset = get_owned(session, Asset, asset_id, ctx.tenant_id, "asset", for_update=True)
    if not asset.is_archived:
        asset.archived_at = clock.now()
        session.flush()
        log.info("asset.archived", extra={"tenant_id": ctx.tenant_id, "asset_id": asset.id})
    return Response(status_code=204)

"""Scan profiles: which plugins run, with which options, on which schedule.

Plugins and ``schedule_cron`` are validated at write time (registry, cron parser)
so a bad profile is refused here instead of failing every scheduled run.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import SessionDep, TenantDep, get_owned
from app.api.pagination import PageDep, paginate
from app.core.errors import Conflict, ValidationFailed
from app.models import ScanProfile
from app.schemas import Page, ScanProfileCreate, ScanProfileRead, ScanProfileUpdate

router = APIRouter(tags=["profiles"])


def _validate(plugins: Iterable[str], plugin_options: Mapping[str, Any], cron: str | None) -> None:
    from app.plugins.registry import available

    wanted = set(plugins)
    unknown = sorted(wanted - set(available()))
    if unknown:
        raise ValidationFailed("unknown plugins", details={"plugins": unknown})
    stray = sorted(set(plugin_options) - wanted)
    if stray:
        raise ValidationFailed(
            "options given for plugins the profile does not run", details={"plugins": stray}
        )
    if cron is not None:
        from app.scheduler.cron import parse_cron

        parse_cron(cron)  # raises ValidationFailed (422)


def _flush(session: Session, name: str) -> None:
    try:
        session.flush()
    except IntegrityError:  # uq (tenant_id, name)
        raise Conflict(f"a profile named {name!r} already exists", details={"name": name}) from None


@router.post("/profiles", status_code=201)
def create_profile(body: ScanProfileCreate, ctx: TenantDep, session: SessionDep) -> ScanProfileRead:
    _validate(body.plugins, body.options.plugins, body.schedule_cron)
    fields = body.model_dump(exclude={"options"})
    profile = ScanProfile(tenant_id=ctx.tenant_id, options=body.options.to_column(), **fields)
    session.add(profile)
    _flush(session, body.name)
    return ScanProfileRead.model_validate(profile)


@router.get("/profiles")
def list_profiles(ctx: TenantDep, session: SessionDep, page: PageDep) -> Page[ScanProfileRead]:
    stmt = select(ScanProfile).where(ScanProfile.tenant_id == ctx.tenant_id)
    return paginate(session, stmt.order_by(ScanProfile.id), page, ScanProfileRead)


@router.get("/profiles/{profile_id}")
def get_profile(profile_id: int, ctx: TenantDep, session: SessionDep) -> ScanProfileRead:
    profile = get_owned(session, ScanProfile, profile_id, ctx.tenant_id, "profile")
    return ScanProfileRead.model_validate(profile)


@router.patch("/profiles/{profile_id}")
def update_profile(
    profile_id: int, body: ScanProfileUpdate, ctx: TenantDep, session: SessionDep
) -> ScanProfileRead:
    """Partial update. ``"schedule_cron": null`` removes the schedule."""
    profile = get_owned(session, ScanProfile, profile_id, ctx.tenant_id, "profile", for_update=True)
    changes = body.model_fields_set
    plugins = body.plugins if body.plugins is not None else profile.plugins
    options = body.options.to_column() if body.options is not None else profile.options
    cron = body.schedule_cron if "schedule_cron" in changes else profile.schedule_cron
    _validate(plugins, (options or {}).get("plugins", {}), cron)

    profile.plugins, profile.options, profile.schedule_cron = list(plugins), options, cron
    if body.name is not None:
        profile.name = body.name
    if body.enabled is not None:
        profile.enabled = body.enabled
    _flush(session, profile.name)
    return ScanProfileRead.model_validate(profile)

"""``limit`` + ``offset`` pagination shared by every list endpoint (since v1.5)."""

from __future__ import annotations

from typing import Annotated, Any, TypeVar

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ValidationFailed
from app.schemas.common import Page, PageParams

S = TypeVar("S", bound=BaseModel)


def page_params(
    limit: Annotated[int | None, Query(ge=1)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PageParams:
    settings = get_settings()
    if limit is None:
        limit = settings.page_limit_default
    if limit > settings.page_limit_max:
        raise ValidationFailed(
            f"limit must not exceed {settings.page_limit_max}", details={"limit": limit}
        )
    # The bounds come from settings, not from PageParams' static defaults.
    return PageParams.model_construct(limit=limit, offset=offset)


PageDep = Annotated[PageParams, Depends(page_params)]


def paginate(session: Session, stmt: Select[Any], page: PageParams, schema: type[S]) -> Page[S]:
    """Run ``stmt`` (already ordered) for one page and count the full result."""
    total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery()))
    rows = session.scalars(stmt.limit(page.limit).offset(page.offset)).all()
    return Page[schema](
        items=[schema.model_validate(row) for row in rows],
        total=total or 0,
        limit=page.limit,
        offset=page.offset,
    )

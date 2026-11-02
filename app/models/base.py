"""Declarative base, naming convention and column helpers shared by all models."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, MappedColumn, mapped_column

from app.core import clock

# Constraint names must match the ones in migrations/versions (written with op.f()).
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def __repr__(self) -> str:
        ident = getattr(self, "id", None)
        return f"<{type(self).__name__} id={ident}>"


def Timestamp(*, nullable: bool = False, default_now: bool = False) -> MappedColumn[Any]:  # noqa: N802
    """A ``timestamptz`` column. ``default_now`` fills it from :func:`app.core.clock.now`
    on the Python side (so frozen clocks apply) and from ``now()`` for raw SQL inserts."""
    if default_now:
        return mapped_column(
            DateTime(timezone=True),
            nullable=nullable,
            default=clock.now,
            server_default=func.now(),
        )
    return mapped_column(DateTime(timezone=True), nullable=nullable)


def enum_check(column: str, enum: type[StrEnum], name: str | None = None) -> CheckConstraint:
    """CHECK constraint limiting a text column to the enum's values."""
    allowed = ", ".join(f"'{member.value}'" for member in enum)
    return CheckConstraint(f"{column} IN ({allowed})", name=name or column)

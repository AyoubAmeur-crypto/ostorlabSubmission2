"""Registered assets: the things a tenant asks us to check."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamp, enum_check
from app.models.enums import AssetKind

if TYPE_CHECKING:
    from app.models.tenant import Tenant


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        enum_check("kind", AssetKind),
        # One live row per hostname and tenant; archived rows keep their history.
        Index(
            "uq_assets_tenant_id_hostname_active",
            "tenant_id",
            "hostname",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    # Normalized by the API: lower case, no trailing dot, IDNA (punycode).
    hostname: Mapped[str] = mapped_column(String(253))
    kind: Mapped[str] = mapped_column(String(8), default=AssetKind.WEB.value)
    # JSON array of short labels, e.g. ["prod", "pci"]. Reassign, do not mutate in place.
    tags: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = Timestamp(default_now=True)
    archived_at: Mapped[datetime | None] = Timestamp(nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="assets")

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    def __repr__(self) -> str:
        return f"<Asset id={self.id} hostname={self.hostname!r}>"

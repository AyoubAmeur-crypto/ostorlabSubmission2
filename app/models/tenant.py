"""Tenants (customer organisations) and their API tokens."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamp

if TYPE_CHECKING:
    from app.models.asset import Asset
    from app.models.scan import ScanProfile
    from app.models.webhook import Webhook


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(63), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    # IANA zone name; schedules are evaluated in this zone.
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC")
    # Ports the tenant expects to be open; anything else is an open_ports finding.
    allowed_ports: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), default=list, server_default=text("'{}'::integer[]")
    )
    created_at: Mapped[datetime] = Timestamp(default_now=True)

    tokens: Mapped[list[ApiToken]] = relationship(back_populates="tenant")
    assets: Mapped[list[Asset]] = relationship(back_populates="tenant")
    profiles: Mapped[list[ScanProfile]] = relationship(back_populates="tenant")
    webhooks: Mapped[list[Webhook]] = relationship(back_populates="tenant")

    def __repr__(self) -> str:
        return f"<Tenant id={self.id} slug={self.slug!r}>"


class ApiToken(Base):
    """A tenant API token. Only the salted sha256 hash is stored; ``prefix`` is
    the first characters of the plaintext and is used to find candidate rows."""

    __tablename__ = "api_tokens"
    __table_args__ = (Index("ix_api_tokens_prefix", "prefix"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    prefix: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64))
    salt: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(100), default="", server_default="")
    created_at: Mapped[datetime] = Timestamp(default_now=True)
    revoked_at: Mapped[datetime | None] = Timestamp(nullable=True)

    tenant: Mapped[Tenant] = relationship(back_populates="tokens")

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

"""Postgres-backed job queue rows (see docs/adr/0001-postgres-queue.md)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamp, enum_check
from app.models.enums import JobStatus


class Job(Base):
    """A unit of background work.

    Claimable when ``status IN ('queued', 'failed') AND run_after <= now()``.
    ``attempts`` counts claims; once a failed attempt reaches ``max_attempts``
    the job becomes ``dead``.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        enum_check("status", JobStatus),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        Index("ix_jobs_status_run_after", "status", "run_after"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(16), default=JobStatus.QUEUED.value, server_default=JobStatus.QUEUED.value
    )
    attempts: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(default=5, server_default=text("5"))
    run_after: Mapped[datetime] = Timestamp(default_now=True)
    locked_by: Mapped[str | None] = mapped_column(String(128))
    locked_at: Mapped[datetime | None] = Timestamp(nullable=True)
    heartbeat_at: Mapped[datetime | None] = Timestamp(nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = Timestamp(default_now=True)
    finished_at: Mapped[datetime | None] = Timestamp(nullable=True)

    def __repr__(self) -> str:
        return f"<Job id={self.id} kind={self.kind} status={self.status}>"

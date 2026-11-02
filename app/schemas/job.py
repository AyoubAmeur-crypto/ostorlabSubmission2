"""Jobs, as shown to operators (CLI, on-call tooling)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.models.enums import JobKind, JobStatus
from app.schemas.common import ReadSchema


class JobRead(ReadSchema):
    id: int
    kind: JobKind | str
    payload: dict[str, Any]
    status: JobStatus
    attempts: int
    max_attempts: int
    run_after: datetime
    locked_by: str | None
    locked_at: datetime | None
    heartbeat_at: datetime | None
    last_error: str | None
    created_at: datetime
    finished_at: datetime | None

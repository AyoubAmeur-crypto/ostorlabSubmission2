"""Liveness for compose and the load balancer. No authentication."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.db import ping

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> JSONResponse:
    db_ok = ping()
    return JSONResponse(
        {"status": "ok" if db_ok else "degraded", "db": db_ok},
        status_code=200 if db_ok else 503,
    )

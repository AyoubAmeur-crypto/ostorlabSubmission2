"""FastAPI application factory. ``uvicorn app.api.main:app`` serves it."""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request, Response

from app import __version__
from app.api.deps import require_admin
from app.api.errors import install_error_handlers
from app.api.ratelimit import FixedWindowRateLimiter, enforce_rate_limit
from app.api.routers import (
    assets,
    findings,
    health,
    profiles,
    reports,
    scans,
    suppression_rules,
    tenants,
    webhooks,
)
from app.core.logging import bind, configure_logging

API_VERSION = __version__  # also in pyproject.toml and CHANGELOG.md
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging("api")
    yield


async def _request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Bind a request id (the caller's, if well formed) to the request's log lines."""
    request_id = request.headers.get("x-request-id", "")
    if not _REQUEST_ID_RE.match(request_id):
        request_id = uuid.uuid4().hex[:16]
    with bind(request_id=request_id):
        response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


def create_app(*, rate_limiter: FixedWindowRateLimiter | None = None) -> FastAPI:
    app = FastAPI(title="Tidewright Orchestrator", version=API_VERSION, lifespan=_lifespan)
    app.state.rate_limiter = rate_limiter or FixedWindowRateLimiter.from_settings()
    install_error_handlers(app)
    app.middleware("http")(_request_context)

    app.include_router(health.router)
    app.include_router(tenants.router, prefix="/v1", dependencies=[Depends(require_admin)])
    limited = [Depends(enforce_rate_limit)]
    for module in (assets, profiles, scans, findings, webhooks, reports, suppression_rules):
        app.include_router(module.router, prefix="/v1", dependencies=limited)
    return app


app = create_app()

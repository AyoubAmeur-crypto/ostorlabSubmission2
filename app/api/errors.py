"""Exception handlers: every error leaves the API as ``{"error": {...}}``.

Services raise ``app.core.errors`` types and we only translate them here.
Stack traces, SQL and request bodies never end up in a response.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import RateLimited, TidewrightError, Unauthorized, ValidationFailed
from app.core.logging import get_logger

log = get_logger(__name__)


async def _tidewright_error(request: Request, exc: TidewrightError) -> JSONResponse:
    headers: dict[str, str] = {}
    if isinstance(exc, RateLimited):
        headers["Retry-After"] = str(exc.retry_after_s)
    if isinstance(exc, Unauthorized):
        headers["WWW-Authenticate"] = "Bearer"
    if exc.status_code >= 500:
        log.error("api.error", extra={"code": exc.code, "path": request.url.path})
    return JSONResponse(exc.to_dict(), status_code=exc.status_code, headers=headers)


async def _validation_error(
    request: Request, exc: RequestValidationError | ValidationError
) -> JSONResponse:
    errors: list[dict[str, Any]] = [
        {"loc": list(err["loc"]), "msg": err["msg"], "type": err["type"]} for err in exc.errors()
    ]
    body = ValidationFailed("request validation failed", details={"errors": errors})
    return JSONResponse(body.to_dict(), status_code=body.status_code)


async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = "not_found" if exc.status_code == 404 else "http_error"
    return JSONResponse(
        {"error": {"code": code, "message": str(exc.detail)}},
        status_code=exc.status_code,
        headers=exc.headers,
    )


async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    log.exception("api.unhandled_error", extra={"path": request.url.path})
    return JSONResponse(
        {"error": {"code": "internal_error", "message": "internal server error"}}, status_code=500
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(TidewrightError, _tidewright_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(ValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unhandled_error)

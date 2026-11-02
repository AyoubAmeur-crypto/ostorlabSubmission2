"""Structured logging.

Every process calls :func:`configure_logging` once at start-up. Log lines are
one JSON object each (``TW_LOG_FORMAT=json``, the default in compose) or a
readable single line (``text``, used by tests).

Pass structured fields with ``extra``; use dotted event names as the message::

    log = get_logger(__name__)
    log.info("scan.started", extra={"scan_id": scan.id, "tenant_id": scan.tenant_id})

Request- or job-wide fields (request_id, tenant_id, job_id) go through
:func:`bind`, which stores them in a context variable picked up by every line
logged inside the block.

Redaction: API tokens (``tw_demo_...``), ``Authorization`` values and any field
whose name looks secret (secret, password, token, signature...) are masked
before formatting. This applies to the message, ``extra`` fields and the
formatted exception text.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

REDACTED = "[REDACTED]"

# Attributes present on every LogRecord; anything else came from ``extra``.
_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}

_SECRET_KEY = re.compile(
    r"(secret|password|passwd|token|api[_-]?key|authorization|signature|cookie)", re.IGNORECASE
)
_TOKEN_RE = re.compile(r"\btw_demo_[A-Za-z0-9_]+")
_BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_KV_SECRET_RE = re.compile(
    r"(?i)\b((?:secret|password|passwd|api[_-]?key|token)\s*[=:]\s*)([^\s,;&\"']+)"
)

_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "tw_log_context", default=None
)


def redact_text(text: str) -> str:
    """Mask tokens and obvious ``key=value`` secrets inside free text."""
    text = _TOKEN_RE.sub("tw_demo_" + REDACTED, text)
    text = _BEARER_RE.sub(lambda m: f"{m.group(1)} {REDACTED}", text)
    return _KV_SECRET_RE.sub(lambda m: m.group(1) + REDACTED, text)


def redact_value(key: str | None, value: Any) -> Any:
    """Redact a structured value. String values under keys that look secret are
    masked entirely; numbers (``tokens_issued=3``) are left alone."""
    if key is not None and _SECRET_KEY.search(key) and isinstance(value, str | bytes) and value:
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {k: redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [redact_value(None, v) for v in value]
    return value


@contextmanager
def bind(**fields: Any) -> Iterator[None]:
    """Attach fields to every log line emitted inside the block (nests)."""
    current = _context.get() or {}
    token = _context.set({**current, **fields})
    try:
        yield
    finally:
        _context.reset(token)


def bound_fields() -> dict[str, Any]:
    return dict(_context.get() or {})


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    fields = bound_fields()
    for key, value in record.__dict__.items():
        if key not in _RESERVED and not key.startswith("_"):
            fields[key] = value
    return {k: redact_value(k, v) for k, v in fields.items()}


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, UTC)
        payload: dict[str, Any] = {
            "ts": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "service": self.service,
            "logger": record.name,
            "msg": redact_text(record.getMessage()),
        }
        payload.update(_extra_fields(record))
        if record.exc_info:
            payload["exc"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, default=_json_default, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, UTC).strftime("%H:%M:%S")
        fields = " ".join(f"{k}={v}" for k, v in _extra_fields(record).items())
        line = f"{ts} {record.levelname:<7} {self.service} {record.name}: "
        line += redact_text(record.getMessage())
        if fields:
            line += f" [{fields}]"
        if record.exc_info:
            line += "\n" + redact_text(self.formatException(record.exc_info))
        return line


def configure_logging(
    service: str | None = None, *, level: str | None = None, fmt: str | None = None
) -> None:
    """Install one stdout handler on the root logger (idempotent)."""
    from app.core.config import get_settings

    settings = get_settings()
    service = service or settings.service_name
    level = (level or settings.log_level).upper()
    fmt = fmt or settings.log_format

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service) if fmt == "json" else TextFormatter(service))
    handler.set_name("tidewright")

    root = logging.getLogger()
    for existing in list(root.handlers):
        if existing.get_name() == "tidewright":
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)
    # uvicorn installs its own handlers; route them through ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

"""Error types shared by every component.

Services raise these; the API turns any :class:`TidewrightError` into a JSON
body ``{"error": {"code": ..., "message": ..., "details": ...}}`` with
``status_code``. Workers log ``code`` and ``message`` and store ``message`` on
the job or scan row. Never put secrets or full tokens in ``message`` or
``details``: both end up in logs and API responses.
"""

from __future__ import annotations

from typing import Any


class TidewrightError(Exception):
    """Base class. Subclasses set ``code`` and ``status_code``."""

    code: str = "internal_error"
    status_code: int = 500

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            body["details"] = self.details
        return {"error": body}

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.message!r})"


class ConfigError(TidewrightError):
    code = "config_error"
    status_code = 500


class ValidationFailed(TidewrightError):
    code = "validation_failed"
    status_code = 422


class Unauthorized(TidewrightError):
    """Missing, malformed, unknown or revoked credentials."""

    code = "unauthorized"
    status_code = 401


class Forbidden(TidewrightError):
    code = "forbidden"
    status_code = 403


class NotFound(TidewrightError):
    """Also used for rows that exist but belong to another tenant."""

    code = "not_found"
    status_code = 404

    @classmethod
    def for_resource(cls, resource: str, resource_id: object) -> NotFound:
        return cls(f"{resource} {resource_id} not found", details={"resource": resource})


class Conflict(TidewrightError):
    code = "conflict"
    status_code = 409


class InvalidTransition(Conflict):
    """A status change the state machine does not allow (e.g. cancel a finished scan)."""

    code = "invalid_transition"

    def __init__(self, entity: str, current: str, target: str) -> None:
        super().__init__(
            f"cannot move {entity} from {current} to {target}",
            details={"entity": entity, "current": current, "target": target},
        )
        self.current = current
        self.target = target


class RateLimited(TidewrightError):
    code = "rate_limited"
    status_code = 429

    def __init__(self, retry_after_s: int) -> None:
        super().__init__("rate limit exceeded", details={"retry_after_s": retry_after_s})
        self.retry_after_s = retry_after_s


class PluginError(TidewrightError):
    """A plugin could not produce a result for an asset. The scan records it;
    ingest is not called for that plugin, so no finding is marked fixed."""

    code = "plugin_error"
    status_code = 500

    def __init__(self, plugin: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(f"{plugin}: {message}", details={"plugin": plugin, **(details or {})})
        self.plugin = plugin


class PluginDataMissing(PluginError):
    """The recorded fixture the plugin needs does not exist for this asset."""

    code = "plugin_data_missing"


class PluginTimeout(PluginError):
    code = "plugin_timeout"

    def __init__(self, plugin: str, timeout_s: float) -> None:
        super().__init__(
            plugin, f"timed out after {timeout_s:g}s", details={"timeout_s": timeout_s}
        )
        self.timeout_s = timeout_s


class UnknownPlugin(NotFound):
    code = "unknown_plugin"

    def __init__(self, name: str) -> None:
        super().__init__(f"unknown plugin {name!r}", details={"plugin": name})
        self.name = name

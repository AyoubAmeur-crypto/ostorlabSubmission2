"""Runtime configuration read from environment variables.

Hand-rolled on purpose (see docs/architecture.md, "Conventions"): we only need
typed reads of a couple dozen variables, and pulling pydantic-settings in for
that was judged not worth another dependency.

Usage::

    from app.core.config import get_settings

    settings = get_settings()
    settings.database_url

Tests change settings with :func:`override_settings` instead of mutating
``os.environ``.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parents[2]

ENVIRONMENTS = ("dev", "test", "prod")
LOG_FORMATS = ("json", "text")
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _str(env: Mapping[str, str], name: str, default: str) -> str:
    value = env.get(name)
    return default if value is None else value.strip()


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{name} must be a boolean (got {raw!r})")


def _int(env: Mapping[str, str], name: str, default: int, *, minimum: int | None = None) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        raise ConfigError(f"{name} must be an integer (got {raw!r})") from None
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum} (got {value})")
    return value


def _float(env: Mapping[str, str], name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        raise ConfigError(f"{name} must be a number (got {raw!r})") from None
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum} (got {value})")
    return value


def _choice(env: Mapping[str, str], name: str, default: str, choices: tuple[str, ...]) -> str:
    value = _str(env, name, default).lower()
    if value not in choices:
        raise ConfigError(f"{name} must be one of {', '.join(choices)} (got {value!r})")
    return value


def _path(env: Mapping[str, str], name: str, default: Path) -> Path:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    path = Path(raw.strip())
    return path if path.is_absolute() else REPO_ROOT / path


@dataclass(frozen=True, slots=True)
class Settings:
    """All tunables in one immutable object. Field docs list the env variable."""

    # TW_ENV: dev | test | prod
    env: str = "dev"
    # TW_SERVICE_NAME: added to every log line (api, worker, scheduler, cli...)
    service_name: str = "tidewright"

    # DATABASE_URL: SQLAlchemy URL, psycopg 3 driver
    database_url: str = "postgresql+psycopg://tidewright:tidewright-demo@db:5432/tidewright"
    # TW_DB_POOL_SIZE / TW_DB_ECHO
    db_pool_size: int = 5
    db_echo: bool = False
    # REDIS_URL
    redis_url: str = "redis://redis:6379/0"

    # TW_LOG_LEVEL / TW_LOG_FORMAT (json | text)
    log_level: str = "INFO"
    log_format: str = "json"

    # TW_ADMIN_TOKEN: bearer token for /v1/admin/* (tenant management). DEMO value.
    admin_token: str = "tw_demo_admin_localdev0000000000"  # noqa: S105 - DEMO value
    # TW_RATE_LIMIT_PER_MINUTE: per-tenant fixed window on the public API
    rate_limit_per_minute: int = 120
    # TW_PAGE_LIMIT_DEFAULT / TW_PAGE_LIMIT_MAX: list endpoints (limit + offset)
    page_limit_default: int = 50
    page_limit_max: int = 500

    # TW_FIXTURES_ROOT: recorded asset data read by the simulated plugins
    fixtures_root: Path = field(default_factory=lambda: REPO_ROOT / "fixtures")
    # TW_REPORTS_DIR: generated report files
    reports_dir: Path = field(default_factory=lambda: REPO_ROOT / "var" / "reports")

    # worker (TW_WORKER_*, TW_JOB_*, TW_RETRY_*, TW_PLUGIN_TIMEOUT_S)
    worker_poll_interval_s: float = 1.0
    worker_heartbeat_s: float = 10.0
    worker_stale_after_s: float = 60.0
    job_max_attempts: int = 5
    retry_base_s: float = 5.0
    retry_max_s: float = 600.0
    plugin_timeout_s: float = 20.0

    # scheduler (TW_SCHEDULER_*)
    scheduler_interval_s: float = 30.0
    scheduler_lookback_s: float = 300.0

    # webhooks (TW_WEBHOOK_*)
    webhook_timeout_s: float = 5.0
    webhook_max_attempts: int = 8

    # TW_DEFAULT_TIMEZONE: used when a tenant row has no usable timezone
    default_timezone: str = "UTC"

    @property
    def is_test(self) -> bool:
        return self.env == "test"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Build settings from a mapping (``os.environ`` by default).

        Raises :class:`ConfigError` with the variable name on any invalid value,
        so a misconfigured container fails at start-up instead of at first use.
        """
        env = os.environ if env is None else env
        defaults = cls()
        settings = cls(
            env=_choice(env, "TW_ENV", defaults.env, ENVIRONMENTS),
            service_name=_str(env, "TW_SERVICE_NAME", defaults.service_name) or "tidewright",
            database_url=_str(env, "DATABASE_URL", defaults.database_url),
            db_pool_size=_int(env, "TW_DB_POOL_SIZE", defaults.db_pool_size, minimum=1),
            db_echo=_bool(env, "TW_DB_ECHO", defaults.db_echo),
            redis_url=_str(env, "REDIS_URL", defaults.redis_url),
            log_level=_str(env, "TW_LOG_LEVEL", defaults.log_level).upper(),
            log_format=_choice(env, "TW_LOG_FORMAT", defaults.log_format, LOG_FORMATS),
            admin_token=_str(env, "TW_ADMIN_TOKEN", defaults.admin_token),
            rate_limit_per_minute=_int(
                env, "TW_RATE_LIMIT_PER_MINUTE", defaults.rate_limit_per_minute, minimum=1
            ),
            page_limit_default=_int(
                env, "TW_PAGE_LIMIT_DEFAULT", defaults.page_limit_default, minimum=1
            ),
            page_limit_max=_int(env, "TW_PAGE_LIMIT_MAX", defaults.page_limit_max, minimum=1),
            fixtures_root=_path(env, "TW_FIXTURES_ROOT", defaults.fixtures_root),
            reports_dir=_path(env, "TW_REPORTS_DIR", defaults.reports_dir),
            worker_poll_interval_s=_float(
                env, "TW_WORKER_POLL_INTERVAL_S", defaults.worker_poll_interval_s
            ),
            worker_heartbeat_s=_float(env, "TW_WORKER_HEARTBEAT_S", defaults.worker_heartbeat_s),
            worker_stale_after_s=_float(
                env, "TW_WORKER_STALE_AFTER_S", defaults.worker_stale_after_s
            ),
            job_max_attempts=_int(env, "TW_JOB_MAX_ATTEMPTS", defaults.job_max_attempts, minimum=1),
            retry_base_s=_float(env, "TW_RETRY_BASE_S", defaults.retry_base_s),
            retry_max_s=_float(env, "TW_RETRY_MAX_S", defaults.retry_max_s),
            plugin_timeout_s=_float(
                env, "TW_PLUGIN_TIMEOUT_S", defaults.plugin_timeout_s, minimum=0.1
            ),
            scheduler_interval_s=_float(
                env, "TW_SCHEDULER_INTERVAL_S", defaults.scheduler_interval_s, minimum=0.1
            ),
            scheduler_lookback_s=_float(
                env, "TW_SCHEDULER_LOOKBACK_S", defaults.scheduler_lookback_s
            ),
            webhook_timeout_s=_float(
                env, "TW_WEBHOOK_TIMEOUT_S", defaults.webhook_timeout_s, minimum=0.1
            ),
            webhook_max_attempts=_int(
                env, "TW_WEBHOOK_MAX_ATTEMPTS", defaults.webhook_max_attempts, minimum=1
            ),
            default_timezone=_str(env, "TW_DEFAULT_TIMEZONE", defaults.default_timezone),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not self.database_url.startswith("postgresql"):
            raise ConfigError("DATABASE_URL must be a postgresql:// URL")
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigError(f"TW_LOG_LEVEL is not a log level (got {self.log_level!r})")
        if self.page_limit_default > self.page_limit_max:
            raise ConfigError("TW_PAGE_LIMIT_DEFAULT must not exceed TW_PAGE_LIMIT_MAX")
        if self.retry_base_s > self.retry_max_s:
            raise ConfigError("TW_RETRY_BASE_S must not exceed TW_RETRY_MAX_S")
        if self.worker_heartbeat_s >= self.worker_stale_after_s:
            raise ConfigError("TW_WORKER_HEARTBEAT_S must be smaller than TW_WORKER_STALE_AFTER_S")
        if self.env == "prod" and self.admin_token.startswith("tw_demo_admin_localdev"):
            raise ConfigError("TW_ADMIN_TOKEN must be changed outside dev/test")
        try:
            ZoneInfo(self.default_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ConfigError(
                f"TW_DEFAULT_TIMEZONE is not an IANA zone (got {self.default_timezone!r})"
            ) from None


_settings: Settings | None = None


def get_settings() -> Settings:
    """Process-wide settings, read from the environment on first call."""
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def reload_settings() -> Settings:
    """Re-read the environment. Used by entrypoints after argument parsing."""
    global _settings
    _settings = Settings.from_env()
    return _settings


@contextmanager
def override_settings(**changes: Any) -> Iterator[Settings]:
    """Temporarily replace fields on the process-wide settings (tests only)."""
    global _settings
    previous = get_settings()
    _settings = dataclasses.replace(previous, **changes)
    try:
        yield _settings
    finally:
        _settings = previous

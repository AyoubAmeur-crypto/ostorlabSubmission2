"""Single source of "now" for the whole service.

Every component calls :func:`now` instead of ``datetime.now()`` /
``datetime.utcnow()`` so tests can freeze or move time (scheduler slots, retry
backoff, heartbeat age, finding timestamps). :func:`now` always returns an
aware datetime in UTC; convert to a tenant's zone only for display or cron
evaluation (``dt.astimezone(ZoneInfo(tenant.timezone))``).

Tests::

    with clock.frozen(datetime(2026, 10, 14, 9, 0, tzinfo=UTC)) as fc:
        ...
        fc.advance(minutes=5)

or the ``frozen_clock`` pytest fixture from tests/conftest.py.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Protocol

__all__ = [
    "UTC",
    "Clock",
    "FrozenClock",
    "SystemClock",
    "ensure_utc",
    "frozen",
    "get_clock",
    "isoformat",
    "now",
    "parse_iso",
    "set_clock",
]


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def __repr__(self) -> str:
        return "SystemClock()"


class FrozenClock:
    """A clock that only moves when told to. Thread-safe."""

    def __init__(self, at: datetime) -> None:
        self._lock = threading.Lock()
        self._at = ensure_utc(at)

    def now(self) -> datetime:
        with self._lock:
            return self._at

    def set(self, at: datetime) -> None:
        with self._lock:
            self._at = ensure_utc(at)

    def advance(self, delta: timedelta | None = None, **kwargs: float) -> datetime:
        """``advance(timedelta(seconds=30))`` or ``advance(seconds=30)``."""
        step = (delta or timedelta()) + timedelta(**kwargs)
        with self._lock:
            self._at = self._at + step
            return self._at

    def __repr__(self) -> str:
        return f"FrozenClock({self._at.isoformat()})"


_clock: Clock = SystemClock()


def now() -> datetime:
    """Current time, aware, in UTC."""
    return _clock.now()


def get_clock() -> Clock:
    return _clock


def set_clock(clock: Clock) -> Clock:
    """Install ``clock`` process-wide and return the previous one."""
    global _clock
    previous = _clock
    _clock = clock
    return previous


@contextmanager
def frozen(at: datetime) -> Iterator[FrozenClock]:
    fc = FrozenClock(at)
    previous = set_clock(fc)
    try:
        yield fc
    finally:
        set_clock(previous)


def ensure_utc(dt: datetime) -> datetime:
    """Return ``dt`` converted to UTC. Naive datetimes are rejected: we never
    guess which zone a naive value was meant to be in."""
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(f"naive datetime not allowed: {dt!r}")
    return dt.astimezone(UTC)


def isoformat(dt: datetime) -> str:
    """Canonical wire format for timestamps: UTC, seconds precision, ``Z`` suffix.

    Used in webhook payloads, reports and log fields so every consumer sees the
    same shape (``2026-10-14T09:00:00Z``).
    """
    return ensure_utc(dt).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp that carries an offset (``Z`` accepted)."""
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    return ensure_utc(datetime.fromisoformat(text))

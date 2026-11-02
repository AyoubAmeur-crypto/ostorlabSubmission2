"""Which cron slots fall inside a time window, on a tenant's wall clock.

Walking the window minute by minute in UTC and converting each instant to the
tenant zone gives the DST behaviour we want: local times that do not exist
(02:00-02:59 on 2026-10-04 in Australia/Sydney) are never produced, so those
slots are skipped that day; repeated local times come back with ``fold=1``
the second time and only the first occurrence fires.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.clock import ensure_utc
from app.core.errors import ValidationFailed
from app.scheduler.cron import parse_cron

_MINUTE = timedelta(minutes=1)


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValidationFailed(
            f"unknown time zone {name!r}", details={"field": "timezone"}
        ) from None


def due_slots(
    cron_expr: str, tz_name: str, window_start: datetime, window_end: datetime
) -> list[datetime]:
    """Aware UTC slot instants with ``window_start < slot <= window_end``, sorted."""
    spec = parse_cron(cron_expr)
    tz = zone(tz_name)
    end = ensure_utc(window_end)
    slot = ensure_utc(window_start).replace(second=0, microsecond=0) + _MINUTE
    slots: list[datetime] = []
    # TODO(ama): the minute walk is cheap for the 5 minute lookback, but a
    # catch-up after a long outage (days) is ~1.4k iterations per day per
    # profile. Jump to the next matching hour if we ever plan further ahead.
    while slot <= end:
        local = slot.astimezone(tz)
        if local.fold == 0 and spec.matches(local):
            slots.append(slot)
        slot += _MINUTE
    return slots

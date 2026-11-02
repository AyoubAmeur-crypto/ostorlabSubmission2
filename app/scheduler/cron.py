"""Five-field cron expressions (``m h dom mon dow``), the subset we support.

Fields take ``*``, ``n``, ``a-b``, ``*/n``, ``a-b/n``, ``a/n`` (a to the field
maximum) and comma lists of those; day of week 0 and 7 are both Sunday. Names
(``MON``) and ``@daily`` are rejected. As in Vixie cron, when day of month and
day of week are both restricted a day matches if either does: ``0 6 1 * 1``
runs on the 1st and on every Monday.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache

from app.core.errors import ValidationFailed

_FIELDS = (("minute", 0, 59), ("hour", 0, 23), ("day of month", 1, 31), ("month", 1, 12),
           ("day of week", 0, 7))  # fmt: skip


@dataclass(frozen=True, slots=True)
class CronSpec:
    expr: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 = Sunday ... 6 = Saturday
    day_restricted: bool
    weekday_restricted: bool

    def matches(self, local: datetime) -> bool:
        """True when the wall-clock minute ``local`` is a slot (seconds ignored)."""
        if not (local.minute in self.minutes and local.hour in self.hours):
            return False
        if local.month not in self.months:
            return False
        day_ok = local.day in self.days
        weekday_ok = local.isoweekday() % 7 in self.weekdays
        if self.day_restricted and self.weekday_restricted:
            return day_ok or weekday_ok
        return day_ok and weekday_ok


def _invalid(expr: str, reason: str) -> ValidationFailed:
    return ValidationFailed(
        f"invalid cron expression {expr!r}: {reason}", details={"field": "schedule_cron"}
    )


def _number(text: str, name: str, expr: str) -> int:
    # isascii(): str.isdigit() accepts characters such as "²" that int() rejects.
    if not (text.isascii() and text.isdigit()):
        raise _invalid(expr, f"{name}: {text!r} is not a number")
    return int(text)


def _parse_field(text: str, name: str, lo: int, hi: int, expr: str) -> frozenset[int]:
    values: set[int] = set()
    for part in text.split(","):
        base, slash, step_text = part.partition("/")
        step = _number(step_text, name, expr) if slash else 1
        if step < 1:
            raise _invalid(expr, f"{name}: step must be at least 1")
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            first, _, last = base.partition("-")
            start, end = _number(first, name, expr), _number(last, name, expr)
        else:
            start = _number(base, name, expr)
            end = hi if slash else start
        if not lo <= start <= end <= hi:
            raise _invalid(expr, f"{name}: {part!r} is reversed or outside {lo}-{hi}")
        values.update(range(start, end + 1, step))
    return frozenset(values)


@lru_cache(maxsize=512)
def parse_cron(expr: str) -> CronSpec:
    """Parse ``expr`` or raise :class:`ValidationFailed` naming the bad field."""
    fields = expr.split()
    if len(fields) != len(_FIELDS):
        raise _invalid(expr, f"expected 5 fields (m h dom mon dow), got {len(fields)}")
    minutes, hours, days, months, weekdays = (
        _parse_field(text, name, lo, hi, expr)
        for text, (name, lo, hi) in zip(fields, _FIELDS, strict=True)
    )
    sunday_is_zero = frozenset(day % 7 for day in weekdays)
    restricted = (not fields[2].startswith("*"), not fields[4].startswith("*"))
    return CronSpec(" ".join(fields), minutes, hours, days, months, sunday_is_zero, *restricted)

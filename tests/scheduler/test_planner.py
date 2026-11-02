from datetime import UTC, datetime

import pytest

from app.core.errors import ValidationFailed
from app.scheduler.planner import due_slots


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


def test_window_excludes_start_and_includes_end():
    slots = due_slots("0 * * * *", "UTC", utc(2026, 10, 14, 9), utc(2026, 10, 14, 11))
    assert slots == [utc(2026, 10, 14, 10), utc(2026, 10, 14, 11)]


def test_sydney_spring_forward_skips_the_missing_slot():
    # 2026-10-04 02:00 AEST -> 03:00 AEDT: 02:30 does not exist that night.
    slots = due_slots("30 2 * * *", "Australia/Sydney", utc(2026, 10, 2, 12), utc(2026, 10, 5))
    assert slots == [utc(2026, 10, 2, 16, 30), utc(2026, 10, 4, 15, 30)]
    after_gap = due_slots("0 3 * * *", "Australia/Sydney", utc(2026, 10, 3, 12), utc(2026, 10, 4))
    assert after_gap == [utc(2026, 10, 3, 16)]  # 03:00 AEDT, once


def test_chicago_fall_back_fires_the_repeated_slot_once():
    # 2026-11-01 02:00 CDT -> 01:00 CST: 01:30 happens at 06:30Z and again at 07:30Z.
    slots = due_slots("30 1 * * *", "America/Chicago", utc(2026, 11, 1), utc(2026, 11, 2))
    assert slots == [utc(2026, 11, 1, 6, 30)]


def test_hourly_schedule_runs_by_wall_clock_through_fall_back():
    slots = due_slots("0 * * * *", "America/Chicago", utc(2026, 11, 1, 5, 30), utc(2026, 11, 1, 9))
    # 01:00 CDT, (01:00 CST is the same wall time: skipped), 02:00 CST, 03:00 CST
    assert slots == [utc(2026, 11, 1, 6), utc(2026, 11, 1, 8), utc(2026, 11, 1, 9)]


@pytest.mark.parametrize(
    ("expr", "tz", "start", "error"),
    [("0 25 * * *", "UTC", utc(2026, 10, 14), ValidationFailed),
     ("* * * * *", "Mars/Olympus", utc(2026, 10, 14), ValidationFailed),
     ("* * * * *", "UTC", datetime(2026, 10, 14), ValueError)],  # naive
)
def test_rejects_bad_input(expr, tz, start, error):
    with pytest.raises(error):
        due_slots(expr, tz, start, utc(2026, 10, 15))

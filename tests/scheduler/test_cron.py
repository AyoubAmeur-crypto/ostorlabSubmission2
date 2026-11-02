from datetime import datetime

import pytest

from app.core.errors import ValidationFailed
from app.scheduler.cron import parse_cron


@pytest.mark.parametrize(
    ("expr", "field", "expected"),
    [
        ("*/15 * * * *", "minutes", {0, 15, 30, 45}),
        ("5,10-12 * * * *", "minutes", {5, 10, 11, 12}),
        ("0 9-17/4 * * *", "hours", {9, 13, 17}),
        ("0 22/1 * * *", "hours", {22, 23}),
        ("0 0 * * 0", "weekdays", {0}),
        ("0 0 * * 7", "weekdays", {0}),
        ("0 0 * * 5-7", "weekdays", {0, 5, 6}),
    ],
)
def test_parses_fields(expr, field, expected):
    assert getattr(parse_cron(expr), field) == frozenset(expected)


INVALID = ["", "* * * *", "* * * * * *", "60 * * * *", "* 24 * * *", "* * 0 * *", "* * * 13 *",
           "* * * * 8", "*/0 * * * *", "*/ * * * *", "5-1 * * * *", "1,,2 * * * *", "-1 * * * *",
           "\u00b2 * * * *", "0 0 * JAN MON"]


@pytest.mark.parametrize("expr", INVALID)
def test_rejects_invalid_expressions(expr):
    with pytest.raises(ValidationFailed):
        parse_cron(expr)


def test_matches_wall_clock_minute_and_seven_is_sunday():
    spec = parse_cron("30 2 * * 7")
    assert spec.matches(datetime(2026, 10, 18, 2, 30, 59))  # Sunday; seconds ignored
    assert not spec.matches(datetime(2026, 10, 19, 2, 30))  # Monday


def test_day_of_month_and_weekday_are_ored_only_when_both_restricted():
    spec = parse_cron("0 6 13 * 5")
    assert spec.matches(datetime(2026, 10, 13, 6, 0))  # Tuesday the 13th
    assert spec.matches(datetime(2026, 10, 16, 6, 0))  # a Friday
    assert not spec.matches(datetime(2026, 10, 14, 6, 0))
    assert not parse_cron("0 6 */2 * 5").matches(datetime(2026, 10, 13, 6, 0))

import threading
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.core import clock


def test_now_is_aware_utc():
    value = clock.now()
    assert value.tzinfo is not None
    assert value.utcoffset() == timedelta(0)


def test_frozen_fixture_pins_world_today(frozen_clock):
    assert clock.now() == datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
    frozen_clock.advance(minutes=90)
    assert clock.now() == datetime(2026, 10, 14, 10, 30, tzinfo=UTC)
    frozen_clock.advance(timedelta(days=1), seconds=5)
    assert clock.now() == datetime(2026, 10, 15, 10, 30, 5, tzinfo=UTC)


def test_frozen_context_restores_previous_clock():
    original = clock.get_clock()
    with clock.frozen(datetime(2026, 3, 29, 0, 59, tzinfo=UTC)) as fc:
        assert clock.get_clock() is fc
    assert clock.get_clock() is original


def test_frozen_clock_converts_offsets_to_utc():
    paris = timezone(timedelta(hours=2))
    fc = clock.FrozenClock(datetime(2026, 10, 14, 11, 0, tzinfo=paris))
    assert fc.now() == datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
    assert fc.now().tzinfo is UTC


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError, match="naive"):
        clock.FrozenClock(datetime(2026, 10, 14, 9, 0))
    with pytest.raises(ValueError, match="naive"):
        clock.ensure_utc(datetime(2026, 10, 14))


def test_isoformat_and_parse_round_trip():
    value = datetime(2026, 10, 14, 9, 0, 12, 345678, tzinfo=UTC)
    text = clock.isoformat(value)
    assert text == "2026-10-14T09:00:12Z"
    assert clock.parse_iso(text) == value.replace(microsecond=0)
    assert clock.parse_iso("2026-10-14T10:00:00+01:00") == datetime(2026, 10, 14, 9, tzinfo=UTC)


def test_parse_iso_requires_offset():
    with pytest.raises(ValueError):
        clock.parse_iso("2026-10-14T10:00:00")


def test_frozen_clock_is_safe_across_threads(frozen_clock):
    def bump():
        for _ in range(1000):
            frozen_clock.advance(seconds=1)

    threads = [threading.Thread(target=bump) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert clock.now() == datetime(2026, 10, 14, 9, 0, tzinfo=UTC) + timedelta(seconds=4000)

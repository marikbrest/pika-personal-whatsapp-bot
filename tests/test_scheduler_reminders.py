"""
compute_next_trigger() is timezone-and-DST-sensitive date math with no
network/DB dependency and a real cost of getting wrong (a reminder that
fires at the wrong time, or not at all) - exactly the kind of pure function
worth testing thoroughly rather than trusting by inspection.

Asia/Jerusalem is UTC+3 in summer (IDT) and UTC+2 in winter (IST); cases
below deliberately span both to catch an accidental hardcoded offset.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.scheduler import compute_next_trigger, format_schedule_description

TZ = ZoneInfo("Asia/Jerusalem")
UTC = ZoneInfo("UTC")


def test_once_in_summer_converts_idt_to_utc_correctly():
    # 2026-07-15 09:00 local (IDT, UTC+3) -> 06:00 UTC
    result = compute_next_trigger("once", "2026-07-15T09:00:00", None, "Asia/Jerusalem")
    assert result == datetime(2026, 7, 15, 6, 0, tzinfo=UTC)


def test_once_in_winter_converts_ist_to_utc_correctly():
    # 2026-01-15 09:00 local (IST, UTC+2) -> 07:00 UTC
    result = compute_next_trigger("once", "2026-01-15T09:00:00", None, "Asia/Jerusalem")
    assert result == datetime(2026, 1, 15, 7, 0, tzinfo=UTC)


def test_daily_stays_today_when_the_time_has_not_passed_yet():
    now = datetime(2026, 7, 15, 8, 0, tzinfo=TZ)  # 08:00 local
    result = compute_next_trigger("daily", "09:00", None, "Asia/Jerusalem", now=now)
    assert result == datetime(2026, 7, 15, 6, 0, tzinfo=UTC)  # 09:00 IDT today


def test_daily_rolls_to_tomorrow_when_the_time_already_passed():
    now = datetime(2026, 7, 15, 10, 0, tzinfo=TZ)  # 10:00 local, past 09:00
    result = compute_next_trigger("daily", "09:00", None, "Asia/Jerusalem", now=now)
    assert result == datetime(2026, 7, 16, 6, 0, tzinfo=UTC)  # tomorrow 09:00 IDT


def test_daily_exact_boundary_rolls_forward_not_fires_immediately():
    """<=, not <, in the source: the exact trigger second must not be treated
    as 'still upcoming today' - that would risk re-firing the reminder it was
    just computed for."""
    now = datetime(2026, 7, 15, 9, 0, 0, tzinfo=TZ)
    result = compute_next_trigger("daily", "09:00", None, "Asia/Jerusalem", now=now)
    assert result == datetime(2026, 7, 16, 6, 0, tzinfo=UTC)


def test_daily_catch_up_after_the_machine_was_off_for_three_days():
    """PRD 12.2 - always relative to `now`, never to a missed
    next_trigger_at, so a 3-day outage produces exactly one future trigger
    (tomorrow), not a backlog of 3."""
    now = datetime(2026, 7, 18, 12, 0, tzinfo=TZ)  # 3 days after the reminder was due
    result = compute_next_trigger("daily", "09:00", None, "Asia/Jerusalem", now=now)
    assert result == datetime(2026, 7, 19, 6, 0, tzinfo=UTC)  # tomorrow, not "3 days of backlog"


def test_weekly_picks_later_this_week_when_time_has_not_passed():
    # 2026-07-15 is a Wednesday
    now = datetime(2026, 7, 15, 8, 0, tzinfo=TZ)
    result = compute_next_trigger("weekly", "09:00", "wed", "Asia/Jerusalem", now=now)
    assert result == datetime(2026, 7, 15, 6, 0, tzinfo=UTC)


def test_weekly_rolls_to_next_week_when_todays_time_already_passed():
    now = datetime(2026, 7, 15, 10, 0, tzinfo=TZ)  # Wednesday, past 09:00
    result = compute_next_trigger("weekly", "09:00", "wed", "Asia/Jerusalem", now=now)
    assert result == datetime(2026, 7, 22, 6, 0, tzinfo=UTC)  # next Wednesday


def test_weekly_picks_the_nearest_of_several_days_not_list_order():
    # 2026-07-15 is Wednesday; days given out of order (fri, mon) - nearest
    # upcoming must be Friday (2 days away), even though 'mon' is later in
    # the input string and alphabetically first.
    now = datetime(2026, 7, 15, 8, 0, tzinfo=TZ)
    result = compute_next_trigger("weekly", "09:00", "fri,mon", "Asia/Jerusalem", now=now)
    assert result == datetime(2026, 7, 17, 6, 0, tzinfo=UTC)  # this Friday


def test_weekly_with_no_days_raises():
    with pytest.raises(ValueError):
        compute_next_trigger("weekly", "09:00", "", "Asia/Jerusalem")
    with pytest.raises(ValueError):
        compute_next_trigger("weekly", "09:00", None, "Asia/Jerusalem")


def test_unsupported_schedule_type_raises():
    with pytest.raises(ValueError):
        compute_next_trigger("hourly", "09:00", None, "Asia/Jerusalem")


def test_defaults_to_asia_jerusalem_when_timezone_name_is_falsy():
    """A user row with a blank timezone must not crash the scheduler - falls
    back to the bot's own default rather than raising on ZoneInfo(None)."""
    now = datetime(2026, 7, 15, 8, 0, tzinfo=TZ)
    result = compute_next_trigger("daily", "09:00", None, "", now=now)
    assert result == datetime(2026, 7, 15, 6, 0, tzinfo=UTC)


def test_format_schedule_description_once_daily_weekly():
    once = format_schedule_description("once", "2026-07-15T09:00:00", None, "Asia/Jerusalem")
    assert "15/07" in once and "09:00" in once

    daily = format_schedule_description("daily", "09:00", None, "Asia/Jerusalem")
    assert "כל יום" in daily and "09:00" in daily

    weekly = format_schedule_description("weekly", "09:00", "mon,wed,fri", "Asia/Jerusalem")
    assert "שני" in weekly and "רביעי" in weekly and "שישי" in weekly

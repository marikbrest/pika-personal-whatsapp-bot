"""
src.tools.batch13 - manage_daily_meetings_summary (new feature, 2026-09-15):
the chat-driven enable/disable/status toggle for the opt-in daily calendar
summary. See webhook_handler._handle_daily_meetings_summary for the actual
logic and scheduler.check_and_send_daily_meetings_summaries for the
proactive morning send this flag drives.

2026-09-17: the send time became per-user configurable (was a single fixed
07:00 for everyone) - a real user (Ronit) asked for 9:00, was told a flat
"7:00" with no acknowledgment of the mismatch, and had no idea her first
summary wouldn't arrive until the NEXT morning since she enabled it a few
minutes after that day's 7:00 slot had already passed. See the time-aware
tests below, which lock in both fixes.
"""
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from src.db.models import (
    get_daily_meetings_summary_enabled,
    get_daily_meetings_summary_time,
    mark_daily_meetings_summary_sent,
)
from src.tools.batch13 import _validate_daily_meetings_summary_args, manage_daily_meetings_summary_tool
from src.tools.registry import tools_for
from src.webhook_handler import _handle_daily_meetings_summary


def test_tool_is_registered_and_not_admin_only():
    assert manage_daily_meetings_summary_tool in tools_for({"is_admin": False})


@pytest.mark.parametrize("args,expected", [
    ({"action": "enable"}, True),
    ({"action": "enable", "time": "09:00"}, True),
    ({"action": "enable", "time": "23:59"}, True),
    ({"action": "enable", "time": "00:00"}, True),
    ({"action": "enable", "time": "9:00"}, False),  # not zero-padded
    ({"action": "enable", "time": "24:00"}, False),  # out of range
    ({"action": "enable", "time": "09:60"}, False),  # out of range
    ({"action": "enable", "time": "בבוקר"}, False),  # not a time at all
    ({"action": "disable"}, True),
    ({"action": "status"}, True),
    ({"action": "unclear-verb"}, False),
    ({}, False),
])
def test_validate(args, expected):
    assert _validate_daily_meetings_summary_args(args) is expected


@pytest.fixture()
def user(make_user):
    return {"id": make_user()}


def test_enable_turns_the_flag_on_and_confirms(user):
    reply = _handle_daily_meetings_summary(user, {"action": "enable"})
    assert "✅" in reply
    assert get_daily_meetings_summary_enabled(user["id"]) is True


def test_enable_with_no_time_defaults_to_0700(user):
    _handle_daily_meetings_summary(user, {"action": "enable"})
    assert get_daily_meetings_summary_time(user["id"]) == "07:00"


def test_enable_with_a_time_sets_it_and_states_it_in_the_confirmation(user):
    reply = _handle_daily_meetings_summary(user, {"action": "enable", "time": "09:00"})
    assert "09:00" in reply
    assert get_daily_meetings_summary_time(user["id"]) == "09:00"


def test_re_enabling_with_a_new_time_changes_it_without_disabling_first(user):
    _handle_daily_meetings_summary(user, {"action": "enable", "time": "07:00"})
    _handle_daily_meetings_summary(user, {"action": "enable", "time": "08:30"})
    assert get_daily_meetings_summary_time(user["id"]) == "08:30"
    assert get_daily_meetings_summary_enabled(user["id"]) is True


def test_re_enabling_with_no_time_keeps_the_previously_set_time(user):
    """Enabling again (e.g. after a temporary disable) without mentioning a
    time must not silently reset a previously chosen time back to 07:00."""
    _handle_daily_meetings_summary(user, {"action": "enable", "time": "09:00"})
    _handle_daily_meetings_summary(user, {"action": "disable"})
    _handle_daily_meetings_summary(user, {"action": "enable"})
    assert get_daily_meetings_summary_time(user["id"]) == "09:00"


def test_disable_turns_the_flag_off_and_confirms(user):
    _handle_daily_meetings_summary(user, {"action": "enable"})
    reply = _handle_daily_meetings_summary(user, {"action": "disable"})
    assert "🔕" in reply
    assert get_daily_meetings_summary_enabled(user["id"]) is False


def test_status_reports_off_by_default(user):
    reply = _handle_daily_meetings_summary(user, {"action": "status"})
    assert "🔕" in reply


def test_status_reports_on_with_the_configured_time_after_enabling(user):
    _handle_daily_meetings_summary(user, {"action": "enable", "time": "09:00"})
    reply = _handle_daily_meetings_summary(user, {"action": "status"})
    assert "✅" in reply
    assert "09:00" in reply


def test_enable_before_todays_slot_says_first_send_is_today(user):
    tz = ZoneInfo("Asia/Jerusalem")
    before_slot = datetime.now(tz).replace(hour=7, minute=0, second=0, microsecond=0) - timedelta(hours=1)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return before_slot

    with patch("src.webhook_handler.datetime", _FixedDatetime):
        reply = _handle_daily_meetings_summary(user, {"action": "enable"})
    assert "הסיכום הראשון יישלח היום" in reply


def test_enable_after_todays_slot_says_first_send_is_tomorrow(user):
    tz = ZoneInfo("Asia/Jerusalem")
    after_slot = datetime.now(tz).replace(hour=7, minute=0, second=0, microsecond=0) + timedelta(minutes=5)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return after_slot

    with patch("src.webhook_handler.datetime", _FixedDatetime):
        reply = _handle_daily_meetings_summary(user, {"action": "enable"})
    assert "הסיכום הראשון יישלח מחר" in reply


def test_enable_with_a_late_custom_time_uses_that_time_for_the_today_tomorrow_check(user):
    """The today/tomorrow check must use the REQUESTED time, not the
    hardcoded default - enabling at 07:30 for a 09:00 slot should still say
    "today", even though 07:30 is past the OLD fixed 07:00 default."""
    tz = ZoneInfo("Asia/Jerusalem")
    just_after_seven = datetime.now(tz).replace(hour=7, minute=30, second=0, microsecond=0)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return just_after_seven

    with patch("src.webhook_handler.datetime", _FixedDatetime):
        reply = _handle_daily_meetings_summary(user, {"action": "enable", "time": "09:00"})
    assert "הסיכום הראשון יישלח היום" in reply


def test_changing_the_time_after_todays_summary_already_sent_clears_the_sent_marker(user):
    """2026-09-18 bug fix: without clearing daily_meetings_summary_last_
    sent_date, check_and_send_daily_meetings_summaries would see today's
    date already recorded (from the OLD time's send) and skip today's send
    under the NEW time entirely - contradicting the "יישלח היום" the
    confirmation reply just promised."""
    tz = ZoneInfo("Asia/Jerusalem")
    today_str = datetime.now(tz).date().isoformat()

    _handle_daily_meetings_summary(user, {"action": "enable", "time": "07:00"})
    mark_daily_meetings_summary_sent(user["id"], today_str)  # simulates today's 07:00 send

    later_today = datetime.now(tz).replace(hour=23, minute=0, second=0, microsecond=0)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return later_today

    with patch("src.webhook_handler.datetime", _FixedDatetime):
        reply = _handle_daily_meetings_summary(user, {"action": "enable", "time": "23:30"})

    assert "הסיכום הראשון יישלח היום" in reply
    from src.db.models import list_users_with_daily_meetings_summary_enabled
    rows = {r["user_id"]: r for r in list_users_with_daily_meetings_summary_enabled()}
    assert rows[user["id"]]["daily_meetings_summary_last_sent_date"] is None


def test_re_enabling_with_no_time_change_does_not_touch_the_sent_marker(user):
    """Only an explicit time change should reset the marker - re-enabling
    (or checking status) without a new time must not risk a duplicate send
    for a summary already sent today under the current time."""
    tz = ZoneInfo("Asia/Jerusalem")
    today_str = datetime.now(tz).date().isoformat()

    _handle_daily_meetings_summary(user, {"action": "enable"})
    mark_daily_meetings_summary_sent(user["id"], today_str)

    _handle_daily_meetings_summary(user, {"action": "enable"})  # no "time" arg

    from src.db.models import list_users_with_daily_meetings_summary_enabled
    rows = {r["user_id"]: r for r in list_users_with_daily_meetings_summary_enabled()}
    assert rows[user["id"]]["daily_meetings_summary_last_sent_date"] == today_str


def test_one_users_toggle_is_isolated_from_another(make_user):
    user_a = {"id": make_user(whatsapp_number="972500000001")}
    user_b = {"id": make_user(whatsapp_number="972500000002")}

    _handle_daily_meetings_summary(user_a, {"action": "enable", "time": "09:00"})

    assert get_daily_meetings_summary_enabled(user_a["id"]) is True
    assert get_daily_meetings_summary_enabled(user_b["id"]) is False
    assert get_daily_meetings_summary_time(user_b["id"]) == "07:00"

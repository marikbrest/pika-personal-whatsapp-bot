"""
check_and_send_kids_schedule_reminders (new feature, 2026-09-15) - the
nightly proactive send built on top of the kids_schedule table. See
tests/test_kids_schedule_models.py for the storage layer and
tests/test_tool_batch12.py for the chat-driven CRUD tool that populates it.
"""
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.db.models import upsert_kid_schedule_day
from src.scheduler import _WEEKDAY_NAMES, check_and_send_kids_schedule_reminders


def _tomorrow_day_of_week(timezone_name: str = "Asia/Jerusalem") -> str:
    tomorrow = datetime.now(ZoneInfo(timezone_name)) + timedelta(days=1)
    return _WEEKDAY_NAMES[tomorrow.weekday()]


def test_sends_nothing_when_no_user_has_saved_a_schedule(db_path, make_user):
    make_user()
    with patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_and_send_kids_schedule_reminders()
    mock_send.assert_not_called()


def test_sends_nothing_for_a_user_whose_only_saved_day_is_not_tomorrow(db_path, make_user):
    user_id = make_user()
    tomorrow = _tomorrow_day_of_week()
    not_tomorrow = next(d for d in _WEEKDAY_NAMES if d != tomorrow)
    upsert_kid_schedule_day(user_id, "דני", not_tomorrow, "חשבון")

    with patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_and_send_kids_schedule_reminders()
    mock_send.assert_not_called()


def test_sends_tomorrows_schedule_for_a_single_kid(db_path, make_user):
    user_id = make_user(whatsapp_number="972500000001")
    tomorrow = _tomorrow_day_of_week()
    upsert_kid_schedule_day(user_id, "דני", tomorrow, "חשבון בשמונה")

    with patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_and_send_kids_schedule_reminders()

    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs["to"] == "972500000001"
    assert "דני" in kwargs["body"]
    assert "חשבון בשמונה" in kwargs["body"]


def test_consolidates_multiple_kids_into_one_message(db_path, make_user):
    user_id = make_user(whatsapp_number="972500000001")
    tomorrow = _tomorrow_day_of_week()
    upsert_kid_schedule_day(user_id, "דני", tomorrow, "חשבון")
    upsert_kid_schedule_day(user_id, "נועה", tomorrow, "ציור")

    with patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_and_send_kids_schedule_reminders()

    mock_send.assert_called_once()
    body = mock_send.call_args.kwargs["body"]
    assert "דני" in body and "חשבון" in body
    assert "נועה" in body and "ציור" in body


def test_one_user_failing_does_not_block_the_next(db_path, make_user):
    """Per-user error isolation, same 12.3 principle as reminders/packages/watches."""
    user_a = make_user(whatsapp_number="972500000001")
    user_b = make_user(whatsapp_number="972500000002")
    tomorrow = _tomorrow_day_of_week()
    upsert_kid_schedule_day(user_a, "דני", tomorrow, "חשבון")
    upsert_kid_schedule_day(user_b, "נועה", tomorrow, "ציור")

    with patch("src.integrations.whatsapp.send_text_message", side_effect=[Exception("boom"), None]) as mock_send:
        check_and_send_kids_schedule_reminders()

    assert mock_send.call_count == 2

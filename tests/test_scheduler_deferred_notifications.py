"""
check_and_deliver_deferred_notifications (2026-09-27, Gatekeeper stage 4) -
re-checks the deterministic delivery policy for anything held by
deliver_proactive_message's defer branch (quiet_hours/status_busy) and
sends it once the window actually opens. Never calls assess_situation again
- the original assess_and_deliver call already made that judgment once.
"""
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.db.models import (
    defer_proactive_message,
    get_connection,
    get_deferred_notifications,
    set_proactive_enabled,
    set_proactive_quiet_hours,
)
from src.scheduler import check_and_deliver_deferred_notifications

TZ = ZoneInfo("Asia/Jerusalem")


def _noon():
    """Fixed daytime moment, safely outside the default 22:30-07:00 quiet
    hours, so these tests don't depend on the real wall-clock time."""
    return patch("src.proactive.datetime", **{"now.return_value": datetime(2026, 1, 1, 12, 0, tzinfo=TZ)})


def test_delivers_a_single_deferred_message_once_allowed(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    set_proactive_enabled(1, True)
    defer_proactive_message(1, "calendar_moved", "אירוע הוזז")

    with _noon():
        with patch("src.integrations.whatsapp.send_text_or_template", return_value=True) as mock_send:
            check_and_deliver_deferred_notifications()

    mock_send.assert_called_once()
    assert mock_send.call_args.kwargs["body"] == "אירוע הוזז"
    assert get_deferred_notifications(1) == []


def test_combines_multiple_deferred_messages_into_one_digest(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    set_proactive_enabled(1, True)
    defer_proactive_message(1, "calendar_moved", "אירוע 1 הוזז")
    defer_proactive_message(1, "email_urgent_vip", "מייל דחוף מהבוס")

    with _noon():
        with patch("src.integrations.whatsapp.send_text_or_template", return_value=True) as mock_send:
            check_and_deliver_deferred_notifications()

    mock_send.assert_called_once()
    body = mock_send.call_args.kwargs["body"]
    assert "אירוע 1 הוזז" in body
    assert "מייל דחוף מהבוס" in body
    assert get_deferred_notifications(1) == []


def test_leaves_messages_deferred_when_still_blocked(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    set_proactive_enabled(1, True)
    set_proactive_quiet_hours(1, "22:30", "07:00")
    defer_proactive_message(1, "calendar_moved", "אירוע הוזז")

    with patch("src.proactive.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 1, 1, 23, 0, tzinfo=TZ)
        with patch("src.integrations.whatsapp.send_text_or_template") as mock_send:
            check_and_deliver_deferred_notifications()

    mock_send.assert_not_called()
    assert len(get_deferred_notifications(1)) == 1


def test_does_not_resend_when_the_underlying_send_fails(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    set_proactive_enabled(1, True)
    defer_proactive_message(1, "calendar_moved", "אירוע הוזז")

    with _noon():
        with patch("src.integrations.whatsapp.send_text_or_template", return_value=False) as mock_send:
            check_and_deliver_deferred_notifications()

    mock_send.assert_called_once()
    # kept for a future retry rather than silently discarded on failure
    assert len(get_deferred_notifications(1)) == 1


def test_stale_deferred_message_is_dropped_without_sending(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    set_proactive_enabled(1, True)
    defer_proactive_message(1, "calendar_moved", "אירוע ישן מדי")

    stale_created_at = (datetime.now(ZoneInfo("UTC")) - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE deferred_proactive_notifications SET created_at = ? WHERE user_id = 1",
            (stale_created_at,),
        )
        conn.commit()
    finally:
        conn.close()

    with _noon():
        with patch("src.integrations.whatsapp.send_text_or_template") as mock_send:
            check_and_deliver_deferred_notifications()

    mock_send.assert_not_called()
    assert get_deferred_notifications(1) == []


def test_stale_message_dropped_while_fresh_message_still_delivered(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    set_proactive_enabled(1, True)
    defer_proactive_message(1, "calendar_moved", "ישן")
    defer_proactive_message(1, "calendar_moved", "טרי")

    stale_created_at = (datetime.now(ZoneInfo("UTC")) - timedelta(hours=25)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE deferred_proactive_notifications SET created_at = ? WHERE body = 'ישן'",
            (stale_created_at,),
        )
        conn.commit()
    finally:
        conn.close()

    with _noon():
        with patch("src.integrations.whatsapp.send_text_or_template", return_value=True) as mock_send:
            check_and_deliver_deferred_notifications()

    mock_send.assert_called_once()
    assert mock_send.call_args.kwargs["body"] == "טרי"
    assert get_deferred_notifications(1) == []


def test_does_nothing_when_there_are_no_deferred_messages(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    set_proactive_enabled(1, True)

    with _noon():
        with patch("src.integrations.whatsapp.send_text_or_template") as mock_send:
            check_and_deliver_deferred_notifications()

    mock_send.assert_not_called()


def test_per_user_error_isolation(db_path, make_user):
    """One user's deferred delivery blowing up must not stop another
    user's from being processed in the same poll."""
    make_user(whatsapp_number="972500000001")
    make_user(whatsapp_number="972500000002")
    set_proactive_enabled(1, True)
    set_proactive_enabled(2, True)
    defer_proactive_message(1, "calendar_moved", "user 1's message")
    defer_proactive_message(2, "calendar_moved", "user 2's message")

    def send_side_effect(to, body, **kwargs):
        if to == "972500000001":
            raise RuntimeError("boom")
        return True

    with _noon():
        with patch("src.integrations.whatsapp.send_text_or_template", side_effect=send_side_effect) as mock_send:
            check_and_deliver_deferred_notifications()

    assert mock_send.call_count == 2
    # user 2 still got delivered despite user 1's failure
    assert get_deferred_notifications(2) == []

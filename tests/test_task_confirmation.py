"""
_check_task_confirmation (new feature, 2026-09-17) - the recipient's half
of a persistent nagging reminder (see src/tools/batch14.py and
scheduler.check_and_send_persistent_reminders for the creation/delivery
side). Deliberately NOT a registered tool - see its own docstring - so
these tests cover both the function directly and its wiring as an early
special-case check in _process_single_message, the same shape
test_webhook_cutover_wiring.py already uses for the cutover call itself.
"""
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.db.models import get_due_persistent_reminders, save_contact, save_persistent_reminder
from src.webhook_handler import _check_task_confirmation

from .conftest import post_webhook, whatsapp_text_payload

NOW = datetime.now(ZoneInfo("UTC"))

PENDING = {
    "id": 1,
    "content": "שיעורי בית",
    "owner_user_id": 1,
    "recipient_name": "דני",
    "owner_whatsapp_number": "972500000001",
    "owner_display_name": "Yossi",
    "schedule_type": "once",
    "schedule_time": None,
    "schedule_days": None,
    "owner_timezone": "Asia/Jerusalem",
}


def test_confirmed_marks_done_and_notifies_owner(db_path, make_user):
    owner_id = make_user(whatsapp_number="972500000001")
    contact_id = save_contact(owner_id, "דני", "972500000071")
    rid = save_persistent_reminder(owner_id, contact_id, "שיעורי בית", NOW)
    pending = {**PENDING, "id": rid, "owner_user_id": owner_id}

    with patch("src.webhook_handler.call_gemini_json", return_value={"confirmed": True}), \
         patch("src.webhook_handler.send_text_message") as mock_send:
        result = _check_task_confirmation("עשיתי", pending, {"id": 99})

    assert result is not None
    assert result["intent"] == "task_confirmation"
    assert "✅" in result["reply"]

    mock_send.assert_called_once()
    assert mock_send.call_args.kwargs["to"] == "972500000001"
    assert "דני" in mock_send.call_args.kwargs["body"]
    assert "שיעורי בית" in mock_send.call_args.kwargs["body"]

    # the row is actually gone from future due-checks
    assert get_due_persistent_reminders(NOW.isoformat()) == []


def test_confirming_a_recurring_reminder_resets_it_for_the_next_occurrence(db_path, make_user):
    """A daily/weekly persistent reminder must NOT terminally resolve on
    confirmation - it resets (attempts_sent back to 0, next_trigger_at
    pushed to the next occurrence) so tomorrow's cycle still happens."""
    owner_id = make_user(whatsapp_number="972500000001", timezone="Asia/Jerusalem")
    contact_id = save_contact(owner_id, "דני", "972500000071")
    rid = save_persistent_reminder(
        owner_id, contact_id, "להאכיל את הכלב", NOW,
        schedule_type="daily", schedule_time="20:00",
    )
    pending = {
        **PENDING, "id": rid, "owner_user_id": owner_id, "content": "להאכיל את הכלב",
        "schedule_type": "daily", "schedule_time": "20:00", "schedule_days": None,
        "owner_timezone": "Asia/Jerusalem",
    }

    with patch("src.webhook_handler.call_gemini_json", return_value={"confirmed": True}), \
         patch("src.webhook_handler.send_text_message"):
        result = _check_task_confirmation("עשיתי", pending, {"id": 99})

    assert result is not None
    # NOT gone - still pending, just reset for its next (tomorrow's) occurrence
    from src.db.models import list_active_persistent_reminders
    rows = list_active_persistent_reminders(owner_id)
    assert len(rows) == 1
    assert rows[0]["attempts_sent"] == 0
    # not due right now (pushed out to tomorrow 20:00)
    assert get_due_persistent_reminders(NOW.isoformat()) == []


def test_not_confirmed_returns_none_and_does_not_notify(db_path):
    with patch("src.webhook_handler.call_gemini_json", return_value={"confirmed": False}), \
         patch("src.webhook_handler.send_text_message") as mock_send:
        result = _check_task_confirmation("מה השעה?", PENDING, {"id": 99})

    assert result is None
    mock_send.assert_not_called()


def test_gemini_failure_is_non_fatal_and_returns_none(db_path):
    with patch("src.webhook_handler.call_gemini_json", side_effect=Exception("boom")), \
         patch("src.webhook_handler.send_text_message") as mock_send:
        result = _check_task_confirmation("עשיתי", PENDING, {"id": 99})

    assert result is None
    mock_send.assert_not_called()


def test_a_confirming_message_skips_the_normal_cutover_entirely(client, make_user):
    """Wiring test (real webhook POST): when the sender is the target of a
    pending nag and their message confirms it, the normal tools cutover
    must never even run - the confirmation short-circuits everything else,
    same as a forwarded-message suggestion does."""
    owner_id = make_user(whatsapp_number="972500000001")
    make_user(whatsapp_number="972500000071", display_name="דני")
    contact_id = save_contact(owner_id, "דני", "972500000071")
    save_persistent_reminder(owner_id, contact_id, "שיעורי בית", NOW)

    with patch("src.webhook_handler.call_gemini_json", return_value={"confirmed": True}), \
         patch("src.webhook_handler._classify_text_with_cutover") as mock_cutover, \
         patch("src.webhook_handler.send_text_message", return_value=True) as mock_send:
        post_webhook(client, whatsapp_text_payload("972500000071", "עשיתי"))

    mock_cutover.assert_not_called()
    # two sends: the owner notification, and the reply to the kid
    assert mock_send.call_count == 2


def test_a_message_from_a_kid_with_nothing_pending_uses_the_normal_cutover(client, make_user):
    """Regression guard: a kid with NO pending nag must be completely
    unaffected - the confirmation check must not even attempt a Gemini call
    when there's nothing to check against."""
    make_user(whatsapp_number="972500000071", display_name="דני")
    fake_result = {"intent": "chat", "reply": "hi"}

    with patch("src.webhook_handler._check_task_confirmation") as mock_check, \
         patch("src.webhook_handler._classify_text_with_cutover", return_value=fake_result), \
         patch("src.webhook_handler.send_text_message", return_value=True):
        post_webhook(client, whatsapp_text_payload("972500000071", "מה קורה"))

    mock_check.assert_not_called()


def test_an_unconfirmed_message_falls_through_to_the_normal_cutover(client, make_user):
    """A pending nag exists, but the message doesn't confirm it (e.g. an
    unrelated question) - normal processing still happens, and the pending
    reminder is untouched (still due on its own schedule)."""
    owner_id = make_user(whatsapp_number="972500000001")
    make_user(whatsapp_number="972500000071", display_name="דני")
    contact_id = save_contact(owner_id, "דני", "972500000071")
    save_persistent_reminder(owner_id, contact_id, "שיעורי בית", NOW)
    fake_result = {"intent": "chat", "reply": "מה קורה?"}

    with patch("src.webhook_handler.call_gemini_json", return_value={"confirmed": False}), \
         patch("src.webhook_handler._classify_text_with_cutover", return_value=fake_result) as mock_cutover, \
         patch("src.webhook_handler.send_text_message", return_value=True):
        post_webhook(client, whatsapp_text_payload("972500000071", "מה השעה?"))

    mock_cutover.assert_called_once()
    # the reminder is still pending - untouched
    assert len(get_due_persistent_reminders(NOW.isoformat())) == 1

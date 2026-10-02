"""
src.tools.batch4 - the fourth batch of the function-calling migration
(reminder, add_contact, connect_google). See batch4.py's module docstring
for why all three ended up renders_own_reply=True instead of the originally
anticipated renders_own_reply=False - reply_text is still a Gemini-authored
schema field either way, just consumed by the handler instead of the
registry's shortcut.

Also covers _handle_reminder/_handle_add_contact/_handle_connect_google
directly (extracted 2026-09-14 from webhook_handler.py's own inline
dispatch, which had no direct test coverage before - this batch is the
first time the contact-not-found reminder path gets a real test).
"""
from unittest.mock import patch

import pytest

from src.tools.batch4 import (
    _validate_add_contact_args,
    _validate_reminder_args,
    add_contact_tool,
    connect_google_tool,
    reminder_tool,
)
from src.tools.dispatch import execute_tool
from src.webhook_handler import _handle_add_contact, _handle_connect_google, _handle_reminder


# --- wiring ---

def test_reminder_tool_dispatches_to_the_real_handler(monkeypatch):
    import src.tools.batch4 as batch4

    monkeypatch.setattr(batch4, "_handle_reminder", lambda user, reminder, reply_text: f"reminder reply: {reply_text}")
    reply = execute_tool(
        reminder_tool, {"id": 1},
        {"content": "x", "schedule_type": "once", "schedule_time": "2026-01-01T09:00:00", "reply_text": "ok!"},
    )
    assert reply == "reminder reply: ok!"


def test_reminder_tool_falls_back_to_fallback_reply_when_reply_text_missing(monkeypatch):
    """Gemini's own schema compliance isn't literally guaranteed (same
    reasoning dispatch.py already applies elsewhere) - a missing reply_text
    must not send an empty WhatsApp message."""
    import src.tools.batch4 as batch4
    from src.intent_parser import FALLBACK_REPLY

    monkeypatch.setattr(batch4, "_handle_reminder", lambda user, reminder, reply_text: reply_text)
    reply = execute_tool(
        reminder_tool, {"id": 1},
        {"content": "x", "schedule_type": "once", "schedule_time": "2026-01-01T09:00:00", "reply_text": ""},
    )
    assert reply == FALLBACK_REPLY


def test_add_contact_tool_dispatches_to_the_real_handler(monkeypatch):
    import src.tools.batch4 as batch4

    monkeypatch.setattr(batch4, "_handle_add_contact", lambda user, contact, reply_text: f"contact reply: {reply_text}")
    reply = execute_tool(add_contact_tool, {"id": 1}, {"name": "Dani", "whatsapp_number": "972501234567", "reply_text": "saved!"})
    assert reply == "contact reply: saved!"


def test_connect_google_tool_dispatches_to_the_real_handler(monkeypatch):
    import src.tools.batch4 as batch4

    monkeypatch.setattr(batch4, "_handle_connect_google", lambda user, reply_text: f"connect reply: {reply_text}")
    reply = execute_tool(connect_google_tool, {"id": 1}, {"reply_text": "sure!"})
    assert reply == "connect reply: sure!"


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"content": "x", "schedule_type": "once", "schedule_time": "t"}, True),
        ({"content": "x", "schedule_type": "once"}, False),
        ({}, False),
    ],
)
def test_validate_reminder_args_matches_intent_parser_rules(args, expected):
    assert _validate_reminder_args(args) is expected


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"name": "Dani", "whatsapp_number": "972501234567"}, True),
        ({"name": "Dani"}, False),
        ({}, False),
    ],
)
def test_validate_add_contact_args_matches_intent_parser_rules(args, expected):
    assert _validate_add_contact_args(args) is expected


def test_add_contact_description_distinguishes_from_user_manage():
    """The one boundary flagged as critical in intent_parser.py's own prompt
    (category 3 vs 12) - a phonebook entry must never be confused with
    granting bot access."""
    assert "does not" in add_contact_tool.description.lower() or "not grant" in add_contact_tool.description.lower()
    assert "access" in add_contact_tool.description.lower()


# --- the extracted handlers themselves, directly ---

def test_handle_reminder_saves_a_reminder_for_the_user_and_returns_reply_text(db_path, make_user):
    user = {"id": make_user(), "timezone": "Asia/Jerusalem"}
    reminder = {"content": "לקחת תרופה", "schedule_type": "once", "schedule_time": "2026-01-01T09:00:00"}

    reply = _handle_reminder(user, reminder, "קבעתי לך תזכורת!")

    assert reply == "קבעתי לך תזכורת!"
    from src.db.models import list_active_reminders
    reminders = list_active_reminders(user["id"])
    assert len(reminders) == 1
    assert reminders[0]["content"] == "לקחת תרופה"


def test_handle_reminder_for_unknown_contact_refuses_and_does_not_save(db_path, make_user):
    """The one behavior that had no test at all before this extraction - it
    was inline dispatch logic with an early return, never independently
    testable."""
    user = {"id": make_user(), "timezone": "Asia/Jerusalem"}
    reminder = {
        "content": "x", "schedule_type": "once", "schedule_time": "2026-01-01T09:00:00",
        "recipient_name": "דני",
    }

    reply = _handle_reminder(user, reminder, "קבעתי לך תזכורת!")

    assert "דני" in reply
    assert "קבעתי" not in reply  # the code-computed refusal, not Gemini's confirmation
    from src.db.models import list_active_reminders
    assert list_active_reminders(user["id"]) == []


def test_handle_reminder_for_known_contact_saves_it(db_path, make_user):
    from src.db.models import save_contact

    user_id = make_user()
    save_contact(user_id, "דני", "972500000099")
    user = {"id": user_id, "timezone": "Asia/Jerusalem"}
    reminder = {
        "content": "x", "schedule_type": "once", "schedule_time": "2026-01-01T09:00:00",
        "recipient_name": "דני",
    }

    reply = _handle_reminder(user, reminder, "קבעתי!")

    assert reply == "קבעתי!"
    from src.db.models import list_active_reminders
    assert len(list_active_reminders(user_id)) == 1


def test_handle_reminder_with_multiple_recipient_names_saves_one_each(db_path, make_user):
    """2026-09-18 bug fix: recipient_name (a single string) silently
    dropped every recipient after the first when several were named at
    once - found live, a real 3-kid request only created a reminder for
    the first-named kid. recipient_names (a list) fixes this."""
    from src.db.models import list_active_reminders, save_contact

    user_id = make_user()
    save_contact(user_id, "דני", "972500000071")
    save_contact(user_id, "נועה", "972500000072")
    user = {"id": user_id, "timezone": "Asia/Jerusalem"}
    reminder = {
        "content": "לנקות", "schedule_type": "daily", "schedule_time": "20:00",
        "recipient_names": ["דני", "נועה"],
    }

    reply = _handle_reminder(user, reminder, "קבעתי!")

    assert reply == "קבעתי!"
    reminders = list_active_reminders(user_id)
    assert len(reminders) == 2
    assert {r["recipient_name"] for r in reminders} == {"דני", "נועה"}


def test_handle_reminder_with_multiple_recipients_refuses_entirely_if_any_unknown(db_path, make_user):
    from src.db.models import list_active_reminders, save_contact

    user_id = make_user()
    save_contact(user_id, "דני", "972500000071")
    user = {"id": user_id, "timezone": "Asia/Jerusalem"}
    reminder = {
        "content": "x", "schedule_type": "once", "schedule_time": "2026-01-01T09:00:00",
        "recipient_names": ["דני", "לא-קיים"],
    }

    reply = _handle_reminder(user, reminder, "קבעתי!")

    assert "לא-קיים" in reply
    assert "קבעתי" not in reply
    assert list_active_reminders(user_id) == []


def test_handle_add_contact_saves_and_returns_reply_text(db_path, make_user):
    user = {"id": make_user()}
    reply = _handle_add_contact(user, {"name": "Dani", "whatsapp_number": "972501234567"}, "נשמר!")

    assert reply == "נשמר!"
    from src.db.models import get_contact_by_name
    assert get_contact_by_name(user["id"], "Dani") is not None


def test_handle_connect_google_appends_the_real_auth_url():
    with patch("src.webhook_handler.build_auth_url", return_value="https://accounts.google.com/fake-auth-url"):
        reply = _handle_connect_google({"id": 1}, "כמובן, רגע קטן!")

    assert reply.startswith("כמובן, רגע קטן!")
    assert "https://accounts.google.com/fake-auth-url" in reply

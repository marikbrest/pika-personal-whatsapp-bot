"""
src.tools.batch14 - manage_persistent_reminders (new feature, 2026-09-17):
the chat-driven create/list/cancel side of a nagging reminder that keeps
repeating until the recipient confirms. See
webhook_handler._handle_persistent_reminders for the actual logic,
scheduler.check_and_send_persistent_reminders for the proactive re-nag +
escalation send, and tests/test_task_confirmation.py for the recipient's
own "I did it" side (handled separately, not by this tool).
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.db.models import (
    MAX_ACTIVE_PERSISTENT_REMINDERS_PER_USER,
    get_due_persistent_reminders,
    list_active_persistent_reminders,
    save_contact,
    save_persistent_reminder,
)
from src.tools.batch14 import _validate_persistent_reminders_args, manage_persistent_reminders_tool
from src.tools.registry import tools_for
from src.webhook_handler import _handle_persistent_reminders


def test_tool_is_registered_and_not_admin_only():
    assert manage_persistent_reminders_tool in tools_for({"is_admin": False})


@pytest.mark.parametrize("args,expected", [
    ({"action": "create", "recipient_name": "דני", "content": "שיעורי בית"}, True),
    ({"action": "create", "recipient_name": "דני"}, False),  # missing content
    ({"action": "create", "content": "שיעורי בית"}, False),  # missing recipient_name
    ({"action": "list"}, True),
    ({"action": "list", "recipient_name": "דני"}, True),
    ({"action": "cancel", "match": "שיעורי בית"}, True),
    ({"action": "cancel"}, False),  # missing match
    ({"action": "unclear-verb"}, False),
    ({}, False),
    (
        {"action": "create", "recipient_name": "דני", "content": "X", "schedule_type": "daily", "schedule_time": "20:00"},
        True,
    ),
    (
        {"action": "create", "recipient_name": "דני", "content": "X", "schedule_type": "daily"},
        False,  # daily missing schedule_time
    ),
    (
        {"action": "create", "recipient_name": "דני", "content": "X", "schedule_type": "weekly", "schedule_time": "08:00", "schedule_days": "mon,tue"},
        True,
    ),
    (
        {"action": "create", "recipient_name": "דני", "content": "X", "schedule_type": "weekly", "schedule_time": "08:00"},
        False,  # weekly missing schedule_days
    ),
    (
        {"action": "create", "recipient_name": "דני", "content": "X", "schedule_type": "monthly", "schedule_time": "08:00"},
        False,  # invalid schedule_type
    ),
])
def test_validate(args, expected):
    assert _validate_persistent_reminders_args(args) is expected


@pytest.fixture()
def user(make_user):
    return {"id": make_user(), "timezone": "Asia/Jerusalem"}


def test_create_refuses_an_unknown_contact(user):
    reply = _handle_persistent_reminders(user, {"action": "create", "recipient_name": "לא-קיים", "content": "X"})
    assert "אין לי את המספר" in reply
    assert list_active_persistent_reminders(user["id"]) == []


def test_create_with_a_known_contact_saves_it_and_confirms(user):
    save_contact(user["id"], "דני", "972500000071")
    reply = _handle_persistent_reminders(user, {"action": "create", "recipient_name": "דני", "content": "שיעורי בית"})
    assert "דני" in reply and "שיעורי בית" in reply
    assert "5 דקות" in reply

    rows = list_active_persistent_reminders(user["id"])
    assert len(rows) == 1
    assert rows[0]["content"] == "שיעורי בית"


def test_create_defaults_to_due_immediately_when_no_start_time_given(user):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    save_contact(user["id"], "דני", "972500000071")
    _handle_persistent_reminders(user, {"action": "create", "recipient_name": "דני", "content": "שיעורי בית"})

    soon = (datetime.now(ZoneInfo("UTC")) + timedelta(seconds=5)).isoformat()
    assert len(get_due_persistent_reminders(soon)) == 1


def test_create_with_a_future_start_time_is_not_due_yet(user):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    save_contact(user["id"], "דני", "972500000071")
    future_local = (datetime.now(ZoneInfo("Asia/Jerusalem")) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
    _handle_persistent_reminders(user, {
        "action": "create", "recipient_name": "דני", "content": "שיעורי בית",
        "schedule_type": "once", "schedule_time": future_local,
    })

    now_utc = datetime.now(ZoneInfo("UTC")).isoformat()
    assert get_due_persistent_reminders(now_utc) == []


def test_create_daily_recurring_saves_the_schedule(user):
    save_contact(user["id"], "דני", "972500000071")
    reply = _handle_persistent_reminders(user, {
        "action": "create", "recipient_name": "דני", "content": "להאכיל את הכלב",
        "schedule_type": "daily", "schedule_time": "20:00",
    })
    assert "דני" in reply and "להאכיל את הכלב" in reply

    rows = list_active_persistent_reminders(user["id"])
    assert len(rows) == 1
    assert rows[0]["schedule_type"] == "daily"
    assert rows[0]["schedule_time"] == "20:00"


def test_create_weekly_recurring_saves_the_days(user):
    save_contact(user["id"], "דני", "972500000071")
    _handle_persistent_reminders(user, {
        "action": "create", "recipient_name": "דני", "content": "לקחת תרופה",
        "schedule_type": "weekly", "schedule_time": "08:00", "schedule_days": "sun,mon,tue",
    })

    rows = list_active_persistent_reminders(user["id"])
    assert rows[0]["schedule_type"] == "weekly"
    assert rows[0]["schedule_days"] == "sun,mon,tue"


def test_create_at_the_cap_returns_a_clear_refusal(user):
    """2026-09-18: the cap check moved from a post-hoc check on
    save_persistent_reminder's return value to a pre-check (counting
    active rows) so a multi-recipient create can refuse the WHOLE request
    up front rather than partially succeeding - so this now seeds real
    rows up to the cap instead of mocking the save to return None."""
    contact_id = save_contact(user["id"], "דני", "972500000071")
    now = datetime.now(ZoneInfo("UTC"))
    for i in range(MAX_ACTIVE_PERSISTENT_REMINDERS_PER_USER):
        save_persistent_reminder(user["id"], contact_id, f"task {i}", now)

    reply = _handle_persistent_reminders(user, {"action": "create", "recipient_name": "דני", "content": "X"})
    assert str(MAX_ACTIVE_PERSISTENT_REMINDERS_PER_USER) in reply


def test_create_with_multiple_recipient_names_creates_one_independent_reminder_each(user):
    """2026-09-18 bug fix regression: a request naming several recipients at
    once must create a SEPARATE reminder for each, not silently drop all
    but the first (found live - a real 3-kid request only created one)."""
    save_contact(user["id"], "דני", "972500000071")
    save_contact(user["id"], "נועה", "972500000072")
    save_contact(user["id"], "תומר", "972500000073")

    reply = _handle_persistent_reminders(
        user, {"action": "create", "recipient_names": ["דני", "נועה", "תומר"], "content": "לנקות את הבית"}
    )

    rows = list_active_persistent_reminders(user["id"])
    assert len(rows) == 3
    assert {r["recipient_name"] for r in rows} == {"דני", "נועה", "תומר"}
    assert all(r["content"] == "לנקות את הבית" for r in rows)
    assert "דני" in reply and "נועה" in reply and "תומר" in reply


def test_create_with_multiple_recipients_refuses_entirely_if_any_is_unknown(user):
    save_contact(user["id"], "דני", "972500000071")

    reply = _handle_persistent_reminders(
        user, {"action": "create", "recipient_names": ["דני", "לא-קיים"], "content": "X"}
    )

    assert "לא-קיים" in reply
    assert list_active_persistent_reminders(user["id"]) == []


def test_create_with_multiple_recipients_refuses_entirely_if_it_would_exceed_the_cap(user):
    contact_id = save_contact(user["id"], "דני", "972500000071")
    save_contact(user["id"], "נועה", "972500000072")
    now = datetime.now(ZoneInfo("UTC"))
    for i in range(MAX_ACTIVE_PERSISTENT_REMINDERS_PER_USER - 1):
        save_persistent_reminder(user["id"], contact_id, f"task {i}", now)

    reply = _handle_persistent_reminders(
        user, {"action": "create", "recipient_names": ["דני", "נועה"], "content": "X"}
    )

    assert str(MAX_ACTIVE_PERSISTENT_REMINDERS_PER_USER) in reply
    assert len(list_active_persistent_reminders(user["id"])) == MAX_ACTIVE_PERSISTENT_REMINDERS_PER_USER - 1


def test_list_on_empty_says_so(user):
    reply = _handle_persistent_reminders(user, {"action": "list"})
    assert "אין לך" in reply


def test_list_shows_recipient_content_and_progress(user):
    save_contact(user["id"], "דני", "972500000071")
    _handle_persistent_reminders(user, {"action": "create", "recipient_name": "דני", "content": "שיעורי בית"})

    reply = _handle_persistent_reminders(user, {"action": "list"})
    assert "דני" in reply
    assert "שיעורי בית" in reply
    assert "0/3" in reply


def test_list_shows_recurring_schedule(user):
    save_contact(user["id"], "דני", "972500000071")
    _handle_persistent_reminders(user, {
        "action": "create", "recipient_name": "דני", "content": "להאכיל את הכלב",
        "schedule_type": "daily", "schedule_time": "20:00",
    })

    reply = _handle_persistent_reminders(user, {"action": "list"})
    assert "כל יום" in reply
    assert "20:00" in reply


def test_list_filtered_by_recipient_excludes_other_kids(user):
    save_contact(user["id"], "דני", "972500000071")
    save_contact(user["id"], "תומר", "972500000073")
    _handle_persistent_reminders(user, {"action": "create", "recipient_name": "דני", "content": "שיעורי בית"})
    _handle_persistent_reminders(user, {"action": "create", "recipient_name": "תומר", "content": "לסדר חדר"})

    reply = _handle_persistent_reminders(user, {"action": "list", "recipient_name": "דני"})
    assert "שיעורי בית" in reply
    assert "לסדר חדר" not in reply


def test_cancel_with_no_match_says_so(user):
    reply = _handle_persistent_reminders(user, {"action": "cancel", "match": "לא-קיים"})
    assert "לא מצאתי" in reply


def test_cancel_stops_the_nag(user):
    save_contact(user["id"], "דני", "972500000071")
    _handle_persistent_reminders(user, {"action": "create", "recipient_name": "דני", "content": "שיעורי בית"})

    reply = _handle_persistent_reminders(user, {"action": "cancel", "match": "שיעורי בית"})
    assert "ביטלתי" in reply
    assert list_active_persistent_reminders(user["id"]) == []


def test_one_users_reminders_are_isolated_from_another(make_user):
    user_a = {"id": make_user(whatsapp_number="972500000001"), "timezone": "Asia/Jerusalem"}
    user_b = {"id": make_user(whatsapp_number="972500000002"), "timezone": "Asia/Jerusalem"}
    save_contact(user_a["id"], "דני", "972500000071")

    _handle_persistent_reminders(user_a, {"action": "create", "recipient_name": "דני", "content": "X"})

    assert len(list_active_persistent_reminders(user_a["id"])) == 1
    assert list_active_persistent_reminders(user_b["id"]) == []


def test_both_parents_can_create_a_reminder_for_the_same_kid(make_user):
    """Yossi's explicit request: both he and Ronit should be able to add
    persistent reminders for the same kid, each via their own saved contact."""
    yossi = {"id": make_user(whatsapp_number="972500000001"), "timezone": "Asia/Jerusalem"}
    ronit = {"id": make_user(whatsapp_number="972500000002"), "timezone": "Asia/Jerusalem"}
    save_contact(yossi["id"], "דני", "972500000071")
    save_contact(ronit["id"], "דני", "972500000071")

    _handle_persistent_reminders(yossi, {"action": "create", "recipient_name": "דני", "content": "שיעורי בית"})
    _handle_persistent_reminders(ronit, {"action": "create", "recipient_name": "דני", "content": "לסדר חדר"})

    assert len(list_active_persistent_reminders(yossi["id"])) == 1
    assert len(list_active_persistent_reminders(ronit["id"])) == 1

"""
src.tools.batch12 - manage_kids_schedule (new feature, 2026-09-15): the
chat-driven CRUD side of a kid's recurring weekly timetable. See
webhook_handler._handle_kids_schedule for the actual logic and
scheduler.check_and_send_kids_schedule_reminders for the proactive evening
send this data feeds.
"""
import pytest

from src.db.models import MAX_KIDS_SCHEDULE_ROWS_PER_USER, get_kid_schedule
from src.tools.batch12 import _validate_kids_schedule_args, manage_kids_schedule_tool
from src.tools.registry import tools_for
from src.webhook_handler import _handle_kids_schedule


def test_tool_is_registered_and_not_admin_only():
    assert manage_kids_schedule_tool in tools_for({"is_admin": False})


@pytest.mark.parametrize("args,expected", [
    ({"action": "set", "kid_name": "דני", "day_of_week": "mon", "content": "חשבון"}, True),
    ({"action": "set", "kid_name": "דני", "day_of_week": "mon"}, False),  # missing content
    ({"action": "set", "kid_name": "דני", "content": "חשבון"}, False),  # missing day_of_week
    ({"action": "set", "kid_name": "דני", "day_of_week": "notaday", "content": "חשבון"}, False),
    ({"action": "set", "day_of_week": "mon", "content": "חשבון"}, False),  # missing kid_name
    ({"action": "list"}, True),
    ({"action": "list", "kid_name": "דני"}, True),
    ({"action": "delete", "kid_name": "דני"}, True),
    ({"action": "delete", "kid_name": "דני", "day_of_week": "tue"}, True),
    ({"action": "delete", "kid_name": "דני", "day_of_week": "notaday"}, False),
    ({"action": "delete"}, False),  # missing kid_name
    ({"action": "unclear-verb"}, False),
    (
        {"action": "set_week", "kid_name": "דני", "days": [
            {"day_of_week": "mon", "content": "חשבון"}, {"day_of_week": "tue", "content": "אנגלית"},
        ]},
        True,
    ),
    ({"action": "set_week", "days": [{"day_of_week": "mon", "content": "חשבון"}]}, False),  # missing kid_name
    ({"action": "set_week", "kid_name": "דני", "days": []}, False),  # empty days
    ({"action": "set_week", "kid_name": "דני"}, False),  # missing days
    (
        {"action": "set_week", "kid_name": "דני", "days": [{"day_of_week": "notaday", "content": "חשבון"}]},
        False,
    ),
    (
        {"action": "set_week", "kid_name": "דני", "days": [{"day_of_week": "mon"}]},  # missing content
        False,
    ),
])
def test_validate(args, expected):
    assert _validate_kids_schedule_args(args) is expected


@pytest.fixture()
def user(make_user):
    number = "972500000001"
    return {"id": make_user(whatsapp_number=number), "whatsapp_number": number}


def test_set_creates_a_row_and_confirms(user):
    reply = _handle_kids_schedule(user, {"action": "set", "kid_name": "דני", "day_of_week": "mon", "content": "חשבון בשמונה"})
    assert "עדכנתי" in reply
    assert "דני" in reply
    assert "חשבון בשמונה" in reply
    rows = get_kid_schedule(user["id"], "דני")
    assert len(rows) == 1 and rows[0]["content"] == "חשבון בשמונה"


def test_set_at_the_cap_returns_a_clear_refusal_not_a_crash(user, monkeypatch):
    import src.webhook_handler as wh

    monkeypatch.setattr(wh, "upsert_kid_schedule_day", lambda *a, **k: False)
    reply = _handle_kids_schedule(user, {"action": "set", "kid_name": "דני", "day_of_week": "mon", "content": "חשבון"})
    assert str(MAX_KIDS_SCHEDULE_ROWS_PER_USER) in reply
    assert "למחוק" in reply


def test_list_on_empty_schedule_says_so_not_an_error(user):
    reply = _handle_kids_schedule(user, {"action": "list"})
    assert "אין עדיין מערכת" in reply


def test_list_groups_by_kid_in_weekday_order(user):
    _handle_kids_schedule(user, {"action": "set", "kid_name": "דני", "day_of_week": "wed", "content": "חשבון"})
    _handle_kids_schedule(user, {"action": "set", "kid_name": "דני", "day_of_week": "mon", "content": "אנגלית"})
    _handle_kids_schedule(user, {"action": "set", "kid_name": "נועה", "day_of_week": "mon", "content": "ציור"})

    reply = _handle_kids_schedule(user, {"action": "list"})
    assert "דני" in reply and "נועה" in reply
    # mon (אנגלית) must be listed before wed (חשבון) within דני's block, matching weekday order
    assert reply.index("אנגלית") < reply.index("חשבון")


def test_list_scoped_to_one_kid_excludes_the_other(user):
    _handle_kids_schedule(user, {"action": "set", "kid_name": "דני", "day_of_week": "mon", "content": "חשבון"})
    _handle_kids_schedule(user, {"action": "set", "kid_name": "נועה", "day_of_week": "mon", "content": "ציור"})

    reply = _handle_kids_schedule(user, {"action": "list", "kid_name": "דני"})
    assert "חשבון" in reply
    assert "נועה" not in reply


def test_delete_one_day(user):
    _handle_kids_schedule(user, {"action": "set", "kid_name": "דני", "day_of_week": "mon", "content": "חשבון"})
    _handle_kids_schedule(user, {"action": "set", "kid_name": "דני", "day_of_week": "tue", "content": "אנגלית"})

    reply = _handle_kids_schedule(user, {"action": "delete", "kid_name": "דני", "day_of_week": "mon"})
    assert "מחקתי" in reply
    remaining = get_kid_schedule(user["id"], "דני")
    assert len(remaining) == 1 and remaining[0]["day_of_week"] == "tue"


def test_delete_whole_kid_when_no_day_given(user):
    _handle_kids_schedule(user, {"action": "set", "kid_name": "דני", "day_of_week": "mon", "content": "חשבון"})
    _handle_kids_schedule(user, {"action": "set", "kid_name": "דני", "day_of_week": "tue", "content": "אנגלית"})

    reply = _handle_kids_schedule(user, {"action": "delete", "kid_name": "דני"})
    assert "מחקתי" in reply
    assert get_kid_schedule(user["id"], "דני") == []


def test_delete_with_nothing_to_delete_says_so(user):
    reply = _handle_kids_schedule(user, {"action": "delete", "kid_name": "לא-קיים"})
    assert "לא מצאתי" in reply


def test_set_week_saves_every_day_in_one_call(user):
    reply = _handle_kids_schedule(user, {
        "action": "set_week", "kid_name": "דני",
        "days": [
            {"day_of_week": "mon", "content": "חשבון בשמונה"},
            {"day_of_week": "wed", "content": "אנגלית בעשר"},
            {"day_of_week": "thu", "content": "ספורט"},
        ],
    })
    assert "שמרתי" in reply
    assert "3" in reply
    rows = {r["day_of_week"]: r["content"] for r in get_kid_schedule(user["id"], "דני")}
    assert rows == {"mon": "חשבון בשמונה", "wed": "אנגלית בעשר", "thu": "ספורט"}


def test_set_week_overwrites_an_existing_day(user):
    _handle_kids_schedule(user, {"action": "set", "kid_name": "דני", "day_of_week": "mon", "content": "ישן"})
    _handle_kids_schedule(user, {
        "action": "set_week", "kid_name": "דני", "days": [{"day_of_week": "mon", "content": "חדש"}],
    })
    rows = get_kid_schedule(user["id"], "דני")
    assert len(rows) == 1 and rows[0]["content"] == "חדש"


def test_set_week_at_the_cap_reports_partial_progress(user, monkeypatch):
    import src.webhook_handler as wh

    calls = {"n": 0}

    def fake_upsert(*a, **k):
        calls["n"] += 1
        return calls["n"] <= 1  # first day succeeds, second hits the cap

    monkeypatch.setattr(wh, "upsert_kid_schedule_day", fake_upsert)
    reply = _handle_kids_schedule(user, {
        "action": "set_week", "kid_name": "דני",
        "days": [{"day_of_week": "mon", "content": "א"}, {"day_of_week": "tue", "content": "ב"}],
    })
    assert "שמרתי חלק" in reply
    assert "שני" in reply  # the day that WAS saved is named


def test_kid_asking_about_their_own_schedule_resolves_via_phone_number(make_user):
    """A registered kid-user with no schedule under their own account should
    see what a parent saved for them, matched via their own WhatsApp number
    against a contact the parent saved (find_kid_schedule_owner_by_whatsapp_number)."""
    parent = {"id": make_user(whatsapp_number="972500000001"), "whatsapp_number": "972500000001"}
    kid_number = "972500000072"
    kid_user = {"id": make_user(whatsapp_number=kid_number, display_name="נועה"), "whatsapp_number": kid_number}

    from src.db.models import save_contact
    save_contact(parent["id"], "נועה", kid_number)
    _handle_kids_schedule(parent, {"action": "set", "kid_name": "נועה", "day_of_week": "mon", "content": "חשבון"})

    reply = _handle_kids_schedule(kid_user, {"action": "list"})
    assert "חשבון" in reply


def test_kid_with_no_saved_schedule_anywhere_gets_the_normal_empty_message(make_user):
    kid_number = "972500000073"
    kid_user = {"id": make_user(whatsapp_number=kid_number, display_name="תומר"), "whatsapp_number": kid_number}

    reply = _handle_kids_schedule(kid_user, {"action": "list"})
    assert "אין עדיין מערכת" in reply


def test_explicit_kid_name_never_triggers_the_phone_fallback(make_user):
    """A parent asking for a specific kid by name must only ever see their
    OWN saved data for that name, never fall through to phone matching."""
    parent = {"id": make_user(whatsapp_number="972500000001"), "whatsapp_number": "972500000001"}
    other_parent = {"id": make_user(whatsapp_number="972500000002"), "whatsapp_number": "972500000002"}

    _handle_kids_schedule(other_parent, {"action": "set", "kid_name": "דני", "day_of_week": "mon", "content": "חשבון"})
    reply = _handle_kids_schedule(parent, {"action": "list", "kid_name": "דני"})
    assert "אין עדיין מערכת" in reply


def test_one_users_schedule_is_isolated_from_another(make_user):
    user_a = {"id": make_user(whatsapp_number="972500000001"), "whatsapp_number": "972500000001"}
    user_b = {"id": make_user(whatsapp_number="972500000002"), "whatsapp_number": "972500000002"}

    _handle_kids_schedule(user_a, {"action": "set", "kid_name": "דני", "day_of_week": "mon", "content": "חשבון"})
    reply = _handle_kids_schedule(user_b, {"action": "list"})
    assert "אין עדיין מערכת" in reply

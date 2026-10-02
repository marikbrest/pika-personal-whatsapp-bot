"""
src.db.models's persistent_reminders table (new feature, 2026-09-17) - a
reminder that keeps re-nagging its recipient every retry_interval_minutes
until they confirm, instead of firing once. See
tests/test_tool_batch14.py for the chat-driven create/list/cancel tool and
tests/test_scheduler_persistent_reminders.py for the proactive re-nag +
escalation send that reads this back.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.db.models import (
    MAX_ACTIVE_PERSISTENT_REMINDERS_PER_USER,
    advance_persistent_reminder,
    cancel_persistent_reminder,
    find_persistent_reminder_by_match,
    find_pending_persistent_reminder_for_number,
    get_due_persistent_reminders,
    list_active_persistent_reminders,
    mark_persistent_reminder_done,
    mark_persistent_reminder_escalated,
    reschedule_persistent_reminder_for_next_occurrence,
    save_contact,
    save_persistent_reminder,
)

NOW = datetime.now(ZoneInfo("UTC"))
PAST = NOW - timedelta(minutes=1)
FUTURE = NOW + timedelta(hours=1)


def _seed(owner_id, contact_id, content="שיעורי בית", trigger=PAST, **kwargs):
    return save_persistent_reminder(owner_id, contact_id, content, trigger, **kwargs)


def test_save_and_get_due(db_path, make_user):
    owner_id = make_user()
    contact_id = save_contact(owner_id, "דני", "972500000071")
    rid = _seed(owner_id, contact_id)
    assert rid is not None

    due = get_due_persistent_reminders(NOW.isoformat())
    assert len(due) == 1
    assert due[0]["id"] == rid
    assert due[0]["recipient_name"] == "דני"
    assert due[0]["recipient_whatsapp_number"] == "972500000071"
    assert due[0]["attempts_sent"] == 0
    assert due[0]["max_attempts"] == 3


def test_not_yet_due_is_excluded(db_path, make_user):
    owner_id = make_user()
    contact_id = save_contact(owner_id, "דני", "972500000071")
    _seed(owner_id, contact_id, trigger=FUTURE)

    assert get_due_persistent_reminders(NOW.isoformat()) == []


def test_cap_blocks_a_new_row_when_at_the_limit(db_path, make_user):
    owner_id = make_user()
    contact_id = save_contact(owner_id, "דני", "972500000071")
    for i in range(MAX_ACTIVE_PERSISTENT_REMINDERS_PER_USER):
        assert _seed(owner_id, contact_id, content=f"task {i}") is not None

    assert _seed(owner_id, contact_id, content="one too many") is None


def test_advance_increments_attempts_and_reschedules(db_path, make_user):
    owner_id = make_user()
    contact_id = save_contact(owner_id, "דני", "972500000071")
    rid = _seed(owner_id, contact_id)

    next_trigger = NOW + timedelta(minutes=5)
    advance_persistent_reminder(rid, next_trigger)

    due = get_due_persistent_reminders(next_trigger.isoformat())
    assert due[0]["attempts_sent"] == 1


def test_mark_done_stops_it_from_being_due(db_path, make_user):
    owner_id = make_user()
    contact_id = save_contact(owner_id, "דני", "972500000071")
    rid = _seed(owner_id, contact_id)

    assert mark_persistent_reminder_done(rid) is True
    assert get_due_persistent_reminders(NOW.isoformat()) == []


def test_mark_done_on_an_already_resolved_row_returns_false(db_path, make_user):
    owner_id = make_user()
    contact_id = save_contact(owner_id, "דני", "972500000071")
    rid = _seed(owner_id, contact_id)

    mark_persistent_reminder_done(rid)
    assert mark_persistent_reminder_done(rid) is False


def test_mark_escalated_stops_it_from_being_due(db_path, make_user):
    owner_id = make_user()
    contact_id = save_contact(owner_id, "דני", "972500000071")
    rid = _seed(owner_id, contact_id)

    mark_persistent_reminder_escalated(rid)
    assert get_due_persistent_reminders(NOW.isoformat()) == []


def test_list_active_only_shows_this_owners_pending_rows(db_path, make_user):
    owner_a = make_user(whatsapp_number="972500000001")
    owner_b = make_user(whatsapp_number="972500000002")
    contact_a = save_contact(owner_a, "דני", "972500000071")
    contact_b = save_contact(owner_b, "תומר", "972500000073")
    _seed(owner_a, contact_a, content="A's task")
    _seed(owner_b, contact_b, content="B's task")

    rows = list_active_persistent_reminders(owner_a)
    assert len(rows) == 1
    assert rows[0]["content"] == "A's task"
    assert rows[0]["recipient_name"] == "דני"


def test_list_active_excludes_resolved_rows(db_path, make_user):
    owner_id = make_user()
    contact_id = save_contact(owner_id, "דני", "972500000071")
    rid = _seed(owner_id, contact_id)
    mark_persistent_reminder_done(rid)

    assert list_active_persistent_reminders(owner_id) == []


def test_find_by_match_on_content(db_path, make_user):
    owner_id = make_user()
    contact_id = save_contact(owner_id, "דני", "972500000071")
    _seed(owner_id, contact_id, content="לעשות שיעורי בית")

    hit = find_persistent_reminder_by_match(owner_id, "שיעורי")
    assert hit is not None
    assert hit["recipient_name"] == "דני"


def test_find_by_match_on_recipient_name(db_path, make_user):
    owner_id = make_user()
    contact_id = save_contact(owner_id, "דני", "972500000071")
    _seed(owner_id, contact_id, content="לעשות שיעורי בית")

    hit = find_persistent_reminder_by_match(owner_id, "דני")
    assert hit is not None


def test_find_by_match_returns_none_on_zero_or_multiple_hits(db_path, make_user):
    owner_id = make_user()
    contact_id = save_contact(owner_id, "דני", "972500000071")
    _seed(owner_id, contact_id, content="task one")
    _seed(owner_id, contact_id, content="task two")

    assert find_persistent_reminder_by_match(owner_id, "לא-קיים") is None
    assert find_persistent_reminder_by_match(owner_id, "task") is None  # matches both


def test_find_by_match_also_matches_when_gemini_combines_name_and_content(db_path, make_user):
    """2026-09-18 regression: found live that Gemini naturally phrases a
    cancel-match as 'KidName content-phrase' combined - not a substring of
    either field alone, but it DOES fully contain both. Must still resolve
    to exactly one row."""
    owner_id = make_user()
    contact_id = save_contact(owner_id, "___LiveTestKidA___", "972500000998")
    _seed(owner_id, contact_id, content="להאכיל את הכלב")

    hit = find_persistent_reminder_by_match(owner_id, "___LiveTestKidA___ להאכיל את הכלב")
    assert hit is not None
    assert hit["content"] == "להאכיל את הכלב"


def test_cancel_is_ownership_scoped(db_path, make_user):
    owner_a = make_user(whatsapp_number="972500000001")
    owner_b = make_user(whatsapp_number="972500000002")
    contact_a = save_contact(owner_a, "דני", "972500000071")
    rid = _seed(owner_a, contact_a)

    assert cancel_persistent_reminder(rid, owner_b) is False  # not the owner
    assert cancel_persistent_reminder(rid, owner_a) is True
    assert get_due_persistent_reminders(NOW.isoformat()) == []


def test_find_pending_for_number_resolves_by_contact_phone(db_path, make_user):
    owner_id = make_user(whatsapp_number="972500000001")
    kid_number = "972500000071"
    contact_id = save_contact(owner_id, "דני", kid_number)
    _seed(owner_id, contact_id, content="שיעורי בית")

    match = find_pending_persistent_reminder_for_number(kid_number, NOW.isoformat())
    assert match is not None
    assert match["content"] == "שיעורי בית"
    assert match["recipient_name"] == "דני"
    assert match["owner_user_id"] == owner_id


def test_find_pending_for_number_returns_none_when_nothing_pending(db_path):
    assert find_pending_persistent_reminder_for_number("972500000099", NOW.isoformat()) is None


def test_find_pending_for_number_ignores_resolved_rows(db_path, make_user):
    owner_id = make_user()
    kid_number = "972500000071"
    contact_id = save_contact(owner_id, "דני", kid_number)
    rid = _seed(owner_id, contact_id)
    mark_persistent_reminder_done(rid)

    assert find_pending_persistent_reminder_for_number(kid_number, NOW.isoformat()) is None


def test_find_pending_for_number_prefers_the_most_recent(db_path, make_user):
    owner_id = make_user()
    kid_number = "972500000071"
    contact_id = save_contact(owner_id, "דני", kid_number)
    _seed(owner_id, contact_id, content="older task")
    _seed(owner_id, contact_id, content="newer task")

    match = find_pending_persistent_reminder_for_number(kid_number, NOW.isoformat())
    assert match["content"] == "newer task"


def test_find_pending_for_number_excludes_a_recurring_reminder_between_occurrences(db_path, make_user):
    """2026-09-18 regression: a daily/weekly reminder stays status='pending'
    forever (see reschedule_persistent_reminder_for_next_occurrence), so it
    must NOT match while its next_trigger_at is still in the future - that
    would make an unrelated message from the kid, sent hours before the
    next nag even goes out, get misread as a task confirmation."""
    owner_id = make_user()
    kid_number = "972500000071"
    contact_id = save_contact(owner_id, "דני", kid_number)
    rid = _seed(owner_id, contact_id, schedule_type="daily", schedule_time="20:00", trigger=PAST)
    reschedule_persistent_reminder_for_next_occurrence(rid, FUTURE)

    assert find_pending_persistent_reminder_for_number(kid_number, NOW.isoformat()) is None


def test_find_pending_for_number_matches_a_recurring_reminder_once_its_cycle_is_due(db_path, make_user):
    owner_id = make_user()
    kid_number = "972500000071"
    contact_id = save_contact(owner_id, "דני", kid_number)
    _seed(owner_id, contact_id, content="להאכיל את הכלב", schedule_type="daily", schedule_time="20:00", trigger=PAST)

    match = find_pending_persistent_reminder_for_number(kid_number, NOW.isoformat())
    assert match is not None
    assert match["content"] == "להאכיל את הכלב"


def test_save_defaults_to_schedule_type_once(db_path, make_user):
    owner_id = make_user()
    contact_id = save_contact(owner_id, "דני", "972500000071")
    rid = _seed(owner_id, contact_id)

    due = get_due_persistent_reminders(NOW.isoformat())
    assert due[0]["schedule_type"] == "once"
    assert due[0]["schedule_time"] is None
    assert due[0]["schedule_days"] is None


def test_save_stores_a_daily_schedule(db_path, make_user):
    owner_id = make_user()
    contact_id = save_contact(owner_id, "דני", "972500000071")
    _seed(owner_id, contact_id, schedule_type="daily", schedule_time="20:00")

    due = get_due_persistent_reminders(NOW.isoformat())
    assert due[0]["schedule_type"] == "daily"
    assert due[0]["schedule_time"] == "20:00"


def test_save_stores_a_weekly_schedule_with_days(db_path, make_user):
    owner_id = make_user()
    contact_id = save_contact(owner_id, "דני", "972500000071")
    _seed(owner_id, contact_id, schedule_type="weekly", schedule_time="08:00", schedule_days="sun,tue")

    due = get_due_persistent_reminders(NOW.isoformat())
    assert due[0]["schedule_type"] == "weekly"
    assert due[0]["schedule_days"] == "sun,tue"


def test_get_due_includes_owner_timezone(db_path, make_user):
    owner_id = make_user(timezone="Asia/Jerusalem")
    contact_id = save_contact(owner_id, "דני", "972500000071")
    _seed(owner_id, contact_id)

    due = get_due_persistent_reminders(NOW.isoformat())
    assert due[0]["owner_timezone"] == "Asia/Jerusalem"


def test_find_pending_for_number_includes_schedule_and_owner_timezone(db_path, make_user):
    owner_id = make_user(timezone="Asia/Jerusalem")
    kid_number = "972500000071"
    contact_id = save_contact(owner_id, "דני", kid_number)
    _seed(owner_id, contact_id, schedule_type="daily", schedule_time="20:00")

    match = find_pending_persistent_reminder_for_number(kid_number, NOW.isoformat())
    assert match["schedule_type"] == "daily"
    assert match["schedule_time"] == "20:00"
    assert match["owner_timezone"] == "Asia/Jerusalem"


def test_reschedule_for_next_occurrence_resets_attempts_and_keeps_pending(db_path, make_user):
    owner_id = make_user()
    contact_id = save_contact(owner_id, "דני", "972500000071")
    rid = _seed(owner_id, contact_id, schedule_type="daily", schedule_time="20:00")
    advance_persistent_reminder(rid, NOW)  # attempts_sent -> 1

    future = NOW + timedelta(days=1)
    assert reschedule_persistent_reminder_for_next_occurrence(rid, future) is True

    # not due now, but is due at the future time - still pending (in list_active)
    assert get_due_persistent_reminders(NOW.isoformat()) == []
    rows = list_active_persistent_reminders(owner_id)
    assert len(rows) == 1
    assert rows[0]["attempts_sent"] == 0
    assert get_due_persistent_reminders(future.isoformat())[0]["id"] == rid


def test_reschedule_does_not_affect_an_already_resolved_row(db_path, make_user):
    owner_id = make_user()
    contact_id = save_contact(owner_id, "דני", "972500000071")
    rid = _seed(owner_id, contact_id)
    mark_persistent_reminder_done(rid)

    assert reschedule_persistent_reminder_for_next_occurrence(rid, NOW + timedelta(days=1)) is False

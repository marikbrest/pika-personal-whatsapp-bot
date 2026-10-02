"""
src/db/models.py (2026-09-27) - the DB layer backing the Gatekeeper's
defer-to-next-free-window feature: a notification blocked by quiet hours or
an explicit "busy" status is held in deferred_proactive_notifications rather
than dropped. These tests cover that table's helpers in isolation.
"""
from src.db.models import (
    defer_proactive_message,
    delete_deferred_notifications,
    get_deferred_notifications,
)


def test_defer_proactive_message_stores_a_row(db_path, make_user):
    make_user(whatsapp_number="972500000001")

    defer_proactive_message(1, "calendar_moved", "אירוע הוזז", identifier="boss@example.com")

    rows = get_deferred_notifications(1)
    assert len(rows) == 1
    assert rows[0]["category"] == "calendar_moved"
    assert rows[0]["body"] == "אירוע הוזז"
    assert rows[0]["identifier"] == "boss@example.com"
    assert rows[0]["created_at"] is not None


def test_defer_proactive_message_identifier_defaults_to_none(db_path, make_user):
    make_user(whatsapp_number="972500000001")

    defer_proactive_message(1, "meeting_prebrief", "פגישה בעוד 15 דקות")

    rows = get_deferred_notifications(1)
    assert rows[0]["identifier"] is None


def test_get_deferred_notifications_returns_only_that_users_rows(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    make_user(whatsapp_number="972500000002")

    defer_proactive_message(1, "calendar_moved", "for user 1")
    defer_proactive_message(2, "calendar_moved", "for user 2")

    rows = get_deferred_notifications(1)
    assert len(rows) == 1
    assert rows[0]["body"] == "for user 1"


def test_get_deferred_notifications_orders_by_created_at(db_path, make_user):
    make_user(whatsapp_number="972500000001")

    defer_proactive_message(1, "calendar_moved", "first")
    defer_proactive_message(1, "calendar_moved", "second")
    defer_proactive_message(1, "calendar_moved", "third")

    rows = get_deferred_notifications(1)
    assert [r["body"] for r in rows] == ["first", "second", "third"]


def test_delete_deferred_notifications_removes_only_the_given_ids(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    defer_proactive_message(1, "calendar_moved", "keep me")
    defer_proactive_message(1, "calendar_moved", "delete me")

    rows = get_deferred_notifications(1)
    to_delete = [r["id"] for r in rows if r["body"] == "delete me"]

    delete_deferred_notifications(to_delete)

    remaining = get_deferred_notifications(1)
    assert len(remaining) == 1
    assert remaining[0]["body"] == "keep me"


def test_delete_deferred_notifications_with_empty_list_is_a_no_op(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    defer_proactive_message(1, "calendar_moved", "keep me")

    delete_deferred_notifications([])

    assert len(get_deferred_notifications(1)) == 1

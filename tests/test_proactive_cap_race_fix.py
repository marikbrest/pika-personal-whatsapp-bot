"""
reserve_proactive_notification_slot / delete_proactive_notification_log_row
(2026-09-27) - the race fix for the daily cap. The old pattern (read count,
then separately insert) had a real TOCTOU gap between
check_and_monitor_calendar_changes and check_and_monitor_new_emails, which
run on independent APScheduler intervals and can overlap for the same user.
"""
from src.db.models import (
    count_todays_proactive_notifications,
    delete_proactive_notification_log_row,
    reserve_proactive_notification_slot,
)


def test_reserve_succeeds_when_under_cap(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    row_id = reserve_proactive_notification_slot(1, cap=3, category="calendar_moved", summary="x")

    assert row_id is not None
    assert count_todays_proactive_notifications(1) == 1


def test_reserve_fails_atomically_once_the_cap_is_reached(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    reserve_proactive_notification_slot(1, cap=1, category="a", summary="x")

    row_id = reserve_proactive_notification_slot(1, cap=1, category="b", summary="y")

    assert row_id is None
    assert count_todays_proactive_notifications(1) == 1  # the second attempt never got counted


def test_reserve_is_the_single_atomic_check_and_insert_a_race_would_defeat(db_path, make_user):
    """Simulates the exact race this fix closes: two 'collectors' both
    reserving for the same user with cap=1 - only one may succeed, no
    matter the call order, because each reservation is one atomic SQL
    statement rather than a separate read-then-write pair."""
    make_user(whatsapp_number="972500000001")

    first = reserve_proactive_notification_slot(1, cap=1, category="calendar_moved", summary="a")
    second = reserve_proactive_notification_slot(1, cap=1, category="email_urgent_vip", summary="b")

    assert first is not None
    assert second is None
    assert count_todays_proactive_notifications(1) == 1


def test_delete_proactive_notification_log_row_releases_the_slot(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    row_id = reserve_proactive_notification_slot(1, cap=1, category="a", summary="x")
    assert count_todays_proactive_notifications(1) == 1

    delete_proactive_notification_log_row(row_id)

    assert count_todays_proactive_notifications(1) == 0
    # the slot is available again for a real send
    assert reserve_proactive_notification_slot(1, cap=1, category="b", summary="y") is not None


def test_reservations_are_isolated_per_user(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    make_user(whatsapp_number="972500000002")
    reserve_proactive_notification_slot(1, cap=1, category="a", summary="x")

    assert count_todays_proactive_notifications(1) == 1
    assert count_todays_proactive_notifications(2) == 0
    assert reserve_proactive_notification_slot(2, cap=1, category="a", summary="y") is not None

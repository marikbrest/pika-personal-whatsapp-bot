"""
delete_old_gmail_seen_messages / delete_old_proactive_notification_log /
scheduler.check_and_cleanup_proactive_data (2026-09-27) - the maintenance
job closing the "grows forever" gap found in the post-build review.
"""
from unittest.mock import patch

from src.db.models import (
    delete_old_gmail_seen_messages,
    delete_old_meeting_prebrief_sent,
    delete_old_proactive_notification_log,
    get_connection,
    mark_gmail_message_seen,
    mark_meeting_prebriefed,
    reserve_proactive_notification_slot,
)
from src.scheduler import check_and_cleanup_proactive_data


def _age_gmail_row(user_id, message_id, days_ago):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE gmail_seen_messages SET created_at = datetime('now', ?) WHERE user_id = ? AND message_id = ?",
            (f"-{days_ago} days", user_id, message_id),
        )
        conn.commit()
    finally:
        conn.close()


def _age_notification_row(row_id, days_ago):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE proactive_notification_log SET created_at = datetime('now', ?) WHERE id = ?",
            (f"-{days_ago} days", row_id),
        )
        conn.commit()
    finally:
        conn.close()


def _age_prebrief_row(user_id, event_id, days_ago):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE meeting_prebrief_sent SET sent_at = datetime('now', ?) WHERE user_id = ? AND event_id = ?",
            (f"-{days_ago} days", user_id, event_id),
        )
        conn.commit()
    finally:
        conn.close()


# ===== delete_old_meeting_prebrief_sent =====

def test_delete_old_meeting_prebrief_sent_removes_only_old_rows(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    mark_meeting_prebriefed(1, "old-event")
    mark_meeting_prebriefed(1, "recent-event")
    _age_prebrief_row(1, "old-event", 90)

    removed = delete_old_meeting_prebrief_sent(60)

    assert removed == 1
    conn = get_connection()
    remaining = {r["event_id"] for r in conn.execute("SELECT event_id FROM meeting_prebrief_sent")}
    conn.close()
    assert remaining == {"recent-event"}


# ===== delete_old_gmail_seen_messages =====

def test_delete_old_gmail_seen_messages_removes_only_old_rows(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    mark_gmail_message_seen(1, "old-message")
    mark_gmail_message_seen(1, "recent-message")
    _age_gmail_row(1, "old-message", 90)

    removed = delete_old_gmail_seen_messages(60)

    assert removed == 1
    conn = get_connection()
    remaining = {r["message_id"] for r in conn.execute("SELECT message_id FROM gmail_seen_messages")}
    conn.close()
    assert remaining == {"recent-message"}


def test_delete_old_gmail_seen_messages_returns_zero_when_nothing_is_old(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    mark_gmail_message_seen(1, "recent-message")
    assert delete_old_gmail_seen_messages(60) == 0


# ===== delete_old_proactive_notification_log =====

def test_delete_old_proactive_notification_log_removes_only_old_rows(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    old_id = reserve_proactive_notification_slot(1, cap=100, category="a", summary="old")
    reserve_proactive_notification_slot(1, cap=100, category="b", summary="recent")
    _age_notification_row(old_id, 90)

    removed = delete_old_proactive_notification_log(60)

    assert removed == 1
    conn = get_connection()
    remaining = [dict(r) for r in conn.execute("SELECT category FROM proactive_notification_log")]
    conn.close()
    assert remaining == [{"category": "b"}]


# ===== check_and_cleanup_proactive_data =====

def test_cleanup_job_calls_both_deletions(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    mark_gmail_message_seen(1, "old-message")
    _age_gmail_row(1, "old-message", 90)

    check_and_cleanup_proactive_data()  # must not raise

    conn = get_connection()
    remaining = conn.execute("SELECT COUNT(*) AS c FROM gmail_seen_messages").fetchone()["c"]
    conn.close()
    assert remaining == 0


def test_cleanup_job_survives_a_db_failure(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    with patch("src.db.models.delete_old_gmail_seen_messages", side_effect=RuntimeError("boom")):
        check_and_cleanup_proactive_data()  # must not raise

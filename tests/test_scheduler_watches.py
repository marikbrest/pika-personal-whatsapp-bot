"""
check_watches (2026-09-14) - the generic watch/trigger engine, structured
the same way test_scheduler_package_giveup.py tests
check_and_notify_package_changes: real DB rows via the db_path/make_user
fixtures, real scheduler function, only the checker and the WhatsApp send
mocked.
"""
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.db import models
from src.scheduler import check_watches

NOW = datetime.now(ZoneInfo("UTC"))


def _seed_watch(user_id, watch_type, target, label, last_state, days_old=1):
    conn = models.get_connection()
    try:
        conn.execute(
            "INSERT INTO watches (user_id, watch_type, target, label, last_state, last_checked_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, watch_type, target, label, last_state,
             (NOW - timedelta(days=days_old)).strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
    finally:
        conn.close()


def test_first_check_establishes_baseline_without_notifying(db_path, make_user):
    """A watch with last_state=NULL (never checked) must not fire a
    notification on its very first check - that would be a false "it
    changed" for something that was simply never observed before."""
    user_id = make_user(whatsapp_number="972500000001")
    _seed_watch(user_id, "web_page", "https://x.com", "https://x.com", last_state=None)

    with patch("src.integrations.watchers.CHECKERS", {"web_page": lambda uid, target: "initial content"}), \
         patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_watches()

    mock_send.assert_not_called()
    watch = models.list_active_watches(user_id)[0]
    assert watch["last_state"] == "initial content"


def test_state_change_sends_exactly_one_notification(db_path, make_user):
    user_id = make_user(whatsapp_number="972500000001")
    _seed_watch(user_id, "web_page", "https://x.com", "the docs page", last_state="old content")

    with patch("src.integrations.watchers.CHECKERS", {"web_page": lambda uid, target: "new content"}), \
         patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_watches()

    mock_send.assert_called_once()
    assert mock_send.call_args.kwargs["to"] == "972500000001"
    assert "the docs page" in mock_send.call_args.kwargs["body"]

    watch = models.list_active_watches(user_id)[0]
    assert watch["last_state"] == "new content"


def test_unchanged_state_sends_no_notification(db_path, make_user):
    user_id = make_user()
    _seed_watch(user_id, "web_page", "https://x.com", "label", last_state="same content")

    with patch("src.integrations.watchers.CHECKERS", {"web_page": lambda uid, target: "same content"}), \
         patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_watches()

    mock_send.assert_not_called()


def test_checker_returning_none_leaves_state_untouched(db_path, make_user):
    """A transient failure (checker returns None) must be retried next
    cycle, not recorded as if it were a real observed state."""
    user_id = make_user()
    _seed_watch(user_id, "web_page", "https://x.com", "label", last_state="old content")

    with patch("src.integrations.watchers.CHECKERS", {"web_page": lambda uid, target: None}), \
         patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_watches()

    mock_send.assert_not_called()
    watch = models.list_active_watches(user_id)[0]
    assert watch["last_state"] == "old content"


def test_one_bad_watch_does_not_block_the_others(db_path, make_user):
    """12.3 per-watch error isolation - a checker exception for one watch
    must not stop the rest of the batch."""
    user_id = make_user(whatsapp_number="972500000001")
    _seed_watch(user_id, "web_page", "https://broken.com", "broken", last_state="old")
    _seed_watch(user_id, "web_page", "https://fine.com", "fine", last_state="old")

    def flaky_checker(uid, target):
        if target == "https://broken.com":
            raise RuntimeError("network exploded")
        return "new"

    with patch("src.integrations.watchers.CHECKERS", {"web_page": flaky_checker}), \
         patch("src.integrations.whatsapp.send_text_message"):
        check_watches()  # must not raise

    states = {w["target"]: w["last_state"] for w in models.list_active_watches(user_id)}
    assert states["https://broken.com"] == "old"  # untouched, will retry next cycle
    assert states["https://fine.com"] == "new"  # still updated despite the sibling failure


def test_inactive_watches_are_never_checked(db_path, make_user):
    user_id = make_user(whatsapp_number="972500000001")
    _seed_watch(user_id, "web_page", "https://x.com", "label", last_state="old")
    conn = models.get_connection()
    try:
        conn.execute("UPDATE watches SET is_active = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()

    # If this watch were checked, "new" != "old" would trigger a notification -
    # so a call proves the inactive row was skipped, not just that the DB flag stuck.
    with patch("src.integrations.watchers.CHECKERS", {"web_page": lambda uid, target: "new"}), \
         patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_watches()

    mock_send.assert_not_called()

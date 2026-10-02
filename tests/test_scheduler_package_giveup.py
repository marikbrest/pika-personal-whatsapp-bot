"""
Formalizes the manual verification run against the live DB on 2026-09-12
when the give-up rule shipped (commit b520f75) and the "tell the user once"
follow-up (2026-09-13 session) into a real, repeatable suite.

2026-09-13 (later same day): status-change notifications now go through
send_text_or_template (the 24h-window template fallback), not
send_text_message directly - see src.scheduler.check_and_notify_package_changes
and src.integrations.whatsapp.send_text_or_template. Every test below that
exercises a status change must mock send_text_or_template, not
send_text_message - mocking the wrong one would let the real call through
with the dummy credentials conftest.py sets, hitting the real Graph API from
the test suite (exactly the kind of incident already documented in
conftest.py's docstring for a different integration).
"""
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from src.db import models
from src.scheduler import (
    _ABANDONED_PACKAGE_STATUS,
    _UNKNOWN_PACKAGE_STATUS,
    _has_never_registered,
    check_and_notify_package_changes,
)

NOW = datetime.now(ZoneInfo("UTC"))


def _pkg(last_status, created_at):
    return {"id": 1, "last_status": last_status, "created_at": created_at}


@pytest.mark.parametrize(
    "label,last_status,created_at,expected",
    [
        ("unknown 8 days old -> give up", _UNKNOWN_PACKAGE_STATUS,
         (NOW - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S"), True),
        ("unknown 6 days old -> keep trying", _UNKNOWN_PACKAGE_STATUS,
         (NOW - timedelta(days=6)).strftime("%Y-%m-%d %H:%M:%S"), False),
        ("in transit 30 days -> not the unknown status at all", "transit",
         (NOW - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S"), False),
        ("unknown, created_at is null -> fail open", _UNKNOWN_PACKAGE_STATUS, None, False),
        ("unknown, unparseable created_at -> fail open, no crash", _UNKNOWN_PACKAGE_STATUS, "not-a-date", False),
        ("never checked (status None) -> not unknown, keep trying", None,
         (NOW - timedelta(days=99)).strftime("%Y-%m-%d %H:%M:%S"), False),
    ],
)
def test_has_never_registered(label, last_status, created_at, expected):
    assert _has_never_registered(_pkg(last_status, created_at), NOW) is expected, label


def _seed_package(user_id, tracking_number, last_status, days_old, description=None):
    """Inserts a tracked_packages row with a backdated created_at/last_status -
    add_tracked_package() alone can't set either (both default on INSERT), and
    the give-up rule is specifically about how OLD the row is."""
    conn = models.get_connection()
    try:
        conn.execute(
            "INSERT INTO tracked_packages (user_id, tracking_number, description, last_status, "
            "last_checked_at, created_at) VALUES (?, ?, ?, ?, NULL, ?)",
            (user_id, tracking_number, description, last_status,
             (NOW - timedelta(days=days_old)).strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
    finally:
        conn.close()


def test_abandons_and_notifies_exactly_once_for_a_stale_unknown_package(db_path, make_user):
    user_id = make_user(whatsapp_number="972500000001")
    _seed_package(user_id, "BADNUMBER123", _UNKNOWN_PACKAGE_STATUS, days_old=8, description="נעליים")

    # The abandoned-package notice has no approved template behind it (it's
    # not one of the three submitted to WhatsApp Manager) - still plain
    # send_text_message, unlike the status-change path below.
    with patch("src.integrations.whatsapp.send_text_message") as mock_send, \
         patch("src.integrations.shipping.get_tracking_status") as mock_ship24:
        check_and_notify_package_changes()

    mock_ship24.assert_not_called()  # the whole point: no wasted API call
    mock_send.assert_called_once()
    assert "972500000001" == mock_send.call_args.kwargs["to"]
    assert "נעליים" in mock_send.call_args.kwargs["body"]

    pkg = models.list_tracked_packages(user_id)[0]
    assert pkg["last_status"] == _ABANDONED_PACKAGE_STATUS

    # Second run: now terminal, must not notify again and must not call Ship24 again.
    with patch("src.integrations.whatsapp.send_text_message") as mock_send2, \
         patch("src.integrations.shipping.get_tracking_status") as mock_ship24_2:
        check_and_notify_package_changes()
    mock_send2.assert_not_called()
    mock_ship24_2.assert_not_called()


def test_delivered_package_is_never_polled_again(db_path, make_user):
    user_id = make_user()
    _seed_package(user_id, "TRACK1", "delivered", days_old=1)

    with patch("src.integrations.shipping.get_tracking_status") as mock_ship24, \
         patch("src.integrations.whatsapp.send_text_message"):
        check_and_notify_package_changes()

    mock_ship24.assert_not_called()


def test_status_change_sends_exactly_one_notification_with_before_and_after(db_path, make_user):
    user_id = make_user(whatsapp_number="972500000001")
    _seed_package(user_id, "TRACK2", "pending", days_old=1, description="חבילה")

    with patch("src.integrations.shipping.get_tracking_status", return_value={"status_milestone": "transit"}), \
         patch("src.integrations.whatsapp.send_text_or_template") as mock_send:
        check_and_notify_package_changes()

    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs["to"] == "972500000001"
    assert "pending" in kwargs["body"] and "transit" in kwargs["body"]

    # package_status_update's three template variables, in order: label, old
    # status, new status - must match exactly what was approved in WhatsApp
    # Manager (see the live template fetch, 2026-09-13).
    assert kwargs["template_name"] == "package_status_update"
    assert kwargs["language_code"] == "he"
    assert kwargs["body_params"] == ["חבילה", "pending", "transit"]

    pkg = models.list_tracked_packages(user_id)[0]
    assert pkg["last_status"] == "transit"


def test_unchanged_status_sends_no_notification(db_path, make_user):
    user_id = make_user()
    _seed_package(user_id, "TRACK3", "transit", days_old=1)

    with patch("src.integrations.shipping.get_tracking_status", return_value={"status_milestone": "transit"}), \
         patch("src.integrations.whatsapp.send_text_or_template") as mock_send:
        check_and_notify_package_changes()

    mock_send.assert_not_called()


def test_one_bad_package_does_not_block_the_others(db_path, make_user):
    """12.3 per-package error isolation - a Ship24 exception for one tracking
    number must not stop the rest of the batch."""
    user_id = make_user(whatsapp_number="972500000001")
    _seed_package(user_id, "BROKEN", "pending", days_old=1)
    _seed_package(user_id, "FINE", "pending", days_old=1)

    def side_effect(tracking_number):
        if tracking_number == "BROKEN":
            raise RuntimeError("Ship24 said no")
        return {"status_milestone": "transit"}

    with patch("src.integrations.shipping.get_tracking_status", side_effect=side_effect), \
         patch("src.integrations.whatsapp.send_text_or_template"):
        check_and_notify_package_changes()  # must not raise

    statuses = {p["tracking_number"]: p["last_status"] for p in models.list_tracked_packages(user_id)}
    assert statuses["BROKEN"] == "pending"  # untouched, will retry next cycle
    assert statuses["FINE"] == "transit"  # still updated despite the sibling failure

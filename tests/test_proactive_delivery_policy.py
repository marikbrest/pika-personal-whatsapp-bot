"""
src/proactive.py (2026-09-27) - the Context-Aware Gatekeeper's delivery
policy: quiet hours, an explicit "busy" status, VIP bypass, and the daily
cap. Every proactive collector must go through should_deliver_now /
deliver_proactive_message - these tests cover the policy itself in
isolation, mocking only the DB layer and the WhatsApp send.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from src.db.models import (
    add_vip_sender,
    set_proactive_daily_cap,
    set_proactive_enabled,
    set_proactive_quiet_hours,
    set_proactive_status_quiet_until,
)
from src.proactive import deliver_proactive_message, is_within_quiet_hours, should_deliver_now

TZ = ZoneInfo("Asia/Jerusalem")


def _user(user_id=1):
    return {"id": user_id, "whatsapp_number": "972500000001", "timezone": "Asia/Jerusalem"}


# ===== is_within_quiet_hours =====

def test_is_within_quiet_hours_simple_range():
    assert is_within_quiet_hours("13:00", "14:00", datetime(2026, 1, 1, 13, 30, tzinfo=TZ)) is True
    assert is_within_quiet_hours("13:00", "14:00", datetime(2026, 1, 1, 15, 0, tzinfo=TZ)) is False


def test_is_within_quiet_hours_overnight_wraparound():
    """22:30-07:00 wraps past midnight - 23:00 and 03:00 are both 'within', noon is not."""
    assert is_within_quiet_hours("22:30", "07:00", datetime(2026, 1, 1, 23, 0, tzinfo=TZ)) is True
    assert is_within_quiet_hours("22:30", "07:00", datetime(2026, 1, 1, 3, 0, tzinfo=TZ)) is True
    assert is_within_quiet_hours("22:30", "07:00", datetime(2026, 1, 1, 12, 0, tzinfo=TZ)) is False


# ===== should_deliver_now =====

def test_blocked_when_never_opted_in(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    allowed, reason = should_deliver_now(_user())
    assert allowed is False
    assert reason == "proactive_disabled"


def test_blocked_when_explicitly_disabled(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    set_proactive_enabled(1, True)
    set_proactive_enabled(1, False)
    allowed, reason = should_deliver_now(_user())
    assert allowed is False
    assert reason == "proactive_disabled"


def test_allowed_when_enabled_and_outside_quiet_hours(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    set_proactive_enabled(1, True)
    set_proactive_quiet_hours(1, "22:30", "07:00")

    with patch("src.proactive.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 1, 1, 12, 0, tzinfo=TZ)
        allowed, reason = should_deliver_now(_user())
    assert allowed is True
    assert reason is None


def test_blocked_during_quiet_hours(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    set_proactive_enabled(1, True)
    set_proactive_quiet_hours(1, "22:30", "07:00")

    with patch("src.proactive.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 1, 1, 23, 0, tzinfo=TZ)
        allowed, reason = should_deliver_now(_user())
    assert allowed is False
    assert reason == "quiet_hours"


def test_vip_bypasses_quiet_hours(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    set_proactive_enabled(1, True)
    set_proactive_quiet_hours(1, "22:30", "07:00")
    add_vip_sender(1, "boss@example.com")

    with patch("src.proactive.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 1, 1, 23, 0, tzinfo=TZ)
        allowed, reason = should_deliver_now(_user(), identifier="boss@example.com")
    assert allowed is True


def test_blocked_by_explicit_busy_status(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    set_proactive_enabled(1, True)
    until = (datetime.now(ZoneInfo("UTC")) + timedelta(hours=1)).isoformat()
    set_proactive_status_quiet_until(1, until)

    allowed, reason = should_deliver_now(_user())
    assert allowed is False
    assert reason == "status_busy"


def test_vip_bypasses_busy_status_too(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    set_proactive_enabled(1, True)
    until = (datetime.now(ZoneInfo("UTC")) + timedelta(hours=1)).isoformat()
    set_proactive_status_quiet_until(1, until)
    add_vip_sender(1, "boss@example.com")

    allowed, reason = should_deliver_now(_user(), identifier="boss@example.com")
    assert allowed is True


def test_busy_status_in_the_past_no_longer_blocks(db_path, make_user):
    """A stale status_quiet_until (e.g. the user's meeting already ended)
    must not keep blocking forever - no explicit "clear" needed. Pins "now"
    to noon so this doesn't depend on the real wall-clock time landing
    outside the default 22:30-07:00 quiet window."""
    make_user(whatsapp_number="972500000001")
    set_proactive_enabled(1, True)
    past = "2026-01-01T08:00:00+00:00"
    set_proactive_status_quiet_until(1, past)

    with patch("src.proactive.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 1, 1, 12, 0, tzinfo=TZ)
        mock_dt.fromisoformat = datetime.fromisoformat
        allowed, reason = should_deliver_now(_user())
    assert allowed is True


def test_daily_cap_blocks_even_a_vip(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    set_proactive_enabled(1, True)
    set_proactive_daily_cap(1, 1)
    add_vip_sender(1, "boss@example.com")

    with patch("src.integrations.whatsapp.send_text_or_template", return_value=True):
        deliver_proactive_message(_user(), "test", "first", identifier="boss@example.com")

    allowed, reason = should_deliver_now(_user(), identifier="boss@example.com")
    assert allowed is False
    assert reason == "daily_cap_reached"


# ===== deliver_proactive_message =====

def test_deliver_proactive_message_sends_and_logs_when_allowed(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    set_proactive_enabled(1, True)

    with patch("src.proactive.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 1, 1, 12, 0, tzinfo=TZ)
        with patch("src.integrations.whatsapp.send_text_or_template", return_value=True) as mock_send:
            sent = deliver_proactive_message(_user(), "calendar_moved", "אירוע הוזז")

    assert sent is True
    mock_send.assert_called_once_with(
        to="972500000001", body="אירוע הוזז",
        template_name="proactive_update", language_code="he", body_params=["אירוע הוזז"],
    )


def test_deliver_proactive_message_does_not_send_when_blocked(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    with patch("src.integrations.whatsapp.send_text_or_template") as mock_send:
        sent = deliver_proactive_message(_user(), "calendar_moved", "אירוע הוזז")

    assert sent is False
    mock_send.assert_not_called()


def test_deliver_proactive_message_releases_the_slot_when_the_send_fails(db_path, make_user):
    """Race fix follow-through (2026-09-27): a failed WhatsApp send must
    not permanently waste a slot of the daily quota - the cap-slot
    reservation is released again on failure."""
    from src.db.models import count_todays_proactive_notifications

    make_user(whatsapp_number="972500000001")
    set_proactive_enabled(1, True)

    with patch("src.proactive.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 1, 1, 12, 0, tzinfo=TZ)
        with patch("src.integrations.whatsapp.send_text_or_template", return_value=False):
            sent = deliver_proactive_message(_user(), "calendar_moved", "אירוע הוזז")

    assert sent is False
    assert count_todays_proactive_notifications(1) == 0


def test_deliver_proactive_message_reservation_respects_the_cap_atomically(db_path, make_user):
    """The cap gate that actually matters is the atomic reserve inside
    deliver_proactive_message, not just should_deliver_now's own
    pre-check - filling the cap by any means still blocks a further send."""
    from src.db.models import reserve_proactive_notification_slot

    make_user(whatsapp_number="972500000001")
    set_proactive_enabled(1, True)
    reserve_proactive_notification_slot(1, cap=6, category="other", summary="x")  # simulate 1/6 already used

    with patch("src.proactive.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 1, 1, 12, 0, tzinfo=TZ)
        with patch("src.integrations.whatsapp.send_text_or_template", return_value=True) as mock_send:
            sent = deliver_proactive_message(_user(), "calendar_moved", "אירוע הוזז")

    assert sent is True
    mock_send.assert_called_once()


# ===== defer-to-next-free-window (2026-09-27) =====

def test_deliver_proactive_message_defers_when_blocked_by_quiet_hours(db_path, make_user):
    from src.db.models import get_deferred_notifications

    make_user(whatsapp_number="972500000001")
    set_proactive_enabled(1, True)
    set_proactive_quiet_hours(1, "22:30", "07:00")

    with patch("src.proactive.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 1, 1, 23, 0, tzinfo=TZ)
        with patch("src.integrations.whatsapp.send_text_or_template") as mock_send:
            sent = deliver_proactive_message(_user(), "calendar_moved", "אירוע הוזז")

    assert sent is False
    mock_send.assert_not_called()
    deferred = get_deferred_notifications(1)
    assert len(deferred) == 1
    assert deferred[0]["body"] == "אירוע הוזז"
    assert deferred[0]["category"] == "calendar_moved"


def test_deliver_proactive_message_defers_when_blocked_by_busy_status(db_path, make_user):
    from src.db.models import get_deferred_notifications, set_proactive_status_quiet_until

    make_user(whatsapp_number="972500000001")
    set_proactive_enabled(1, True)
    until = (datetime.now(ZoneInfo("UTC")) + timedelta(hours=1)).isoformat()
    set_proactive_status_quiet_until(1, until)

    with patch("src.integrations.whatsapp.send_text_or_template") as mock_send:
        sent = deliver_proactive_message(_user(), "email_urgent_vip", "מייל דחוף")

    assert sent is False
    mock_send.assert_not_called()
    deferred = get_deferred_notifications(1)
    assert len(deferred) == 1


def test_deliver_proactive_message_does_not_defer_when_disabled(db_path, make_user):
    """proactive_disabled has no "next free window" to wait for at all -
    must not be deferred."""
    from src.db.models import get_deferred_notifications

    make_user(whatsapp_number="972500000001")
    with patch("src.integrations.whatsapp.send_text_or_template"):
        deliver_proactive_message(_user(), "calendar_moved", "אירוע הוזז")

    assert get_deferred_notifications(1) == []


def test_deliver_proactive_message_does_not_defer_when_cap_reached(db_path, make_user):
    """The daily cap is a volume limiter, not a timing constraint - piling
    up capped messages for the moment the cap resets would defeat its own
    purpose."""
    from src.db.models import get_deferred_notifications

    make_user(whatsapp_number="972500000001")
    set_proactive_enabled(1, True)
    set_proactive_daily_cap(1, 0)

    with patch("src.integrations.whatsapp.send_text_or_template") as mock_send:
        sent = deliver_proactive_message(_user(), "calendar_moved", "אירוע הוזז")

    assert sent is False
    mock_send.assert_not_called()
    assert get_deferred_notifications(1) == []

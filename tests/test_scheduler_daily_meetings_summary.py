"""
check_and_send_daily_meetings_summaries (new feature, 2026-09-15) - the
proactive send built on top of the daily_meetings_summary_enabled opt-in
flag. Reuses morning_brief._calendar_section rather than reimplementing the
fetch/format, so these tests mock that function directly (same boundary
morning_brief.py's own tests would mock at) rather than the underlying
Google Calendar integration.

2026-09-17: the send time became per-user configurable (was a single fixed
07:00 cron slot for everyone), so this job now polls frequently and
compares each user's own configured time - see the "due"/"not due yet"/
"already sent today" tests below, which didn't exist under the old
fixed-cron design. Also: _calendar_section now RAISES NotConnectedError/
GoogleAuthExpiredError instead of swallowing them (see morning_brief.py),
so a "calendar sync failed" morning gets an explicit message with the
reconnect link instead of silent skipping - but only after a retry (see the
transient-recovery test), and both a genuine failure and a real send now
mark daily_meetings_summary_last_sent_date so the next poll (minutes later)
doesn't repeat it.
"""
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.db.models import (
    get_connection,
    mark_daily_meetings_summary_sent,
    set_daily_meetings_summary_enabled,
    set_daily_meetings_summary_time,
)
from src.integrations.google_oauth import GoogleAuthExpiredError, NotConnectedError
from src.scheduler import check_and_send_daily_meetings_summaries

TZ = ZoneInfo("Asia/Jerusalem")


def _set_last_sent_date(user_id, date_str):
    """Direct DB write for date strings not exposed by mark_daily_meetings_
    summary_sent's own "today" framing - only used to set up a PAST date
    for the "due again today, not yet sent" test cases below."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE users SET daily_meetings_summary_last_sent_date = ? WHERE id = ?",
            (date_str, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_sends_nothing_when_no_user_opted_in(db_path, make_user):
    make_user()
    with patch("src.morning_brief._calendar_section") as mock_section, \
         patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_and_send_daily_meetings_summaries()
    mock_section.assert_not_called()
    mock_send.assert_not_called()


def test_not_due_yet_today_sends_nothing(db_path, make_user):
    """The user's configured time is in the future (their own local time) -
    no send, and _calendar_section shouldn't even be called (no point
    fetching a summary that isn't due)."""
    user_id = make_user(whatsapp_number="972500000001")
    set_daily_meetings_summary_enabled(user_id, True)
    # Clamped to stay within today - "+2 hours" alone crosses midnight (and
    # therefore wraps to an HH:MM earlier than "now") whenever the test runs
    # between 22:00 and 23:59 local time. Found live: this genuinely failed
    # when the whole suite happened to run at 23:08.
    now = datetime.now(TZ)
    future_dt = min(now + timedelta(hours=2), now.replace(hour=23, minute=59, second=0, microsecond=0))
    future_time = future_dt.strftime("%H:%M")
    set_daily_meetings_summary_time(user_id, future_time)

    with patch("src.morning_brief._calendar_section") as mock_section, \
         patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_and_send_daily_meetings_summaries()

    mock_section.assert_not_called()
    mock_send.assert_not_called()


def test_already_sent_today_does_not_send_again(db_path, make_user):
    """Due time has passed, but last_sent_date is already today - a later
    poll in the same day must not re-send."""
    user_id = make_user(whatsapp_number="972500000001")
    set_daily_meetings_summary_enabled(user_id, True)
    past_time = (datetime.now(TZ) - timedelta(hours=1)).strftime("%H:%M")
    set_daily_meetings_summary_time(user_id, past_time)
    mark_daily_meetings_summary_sent(user_id, datetime.now(TZ).date().isoformat())

    with patch("src.morning_brief._calendar_section") as mock_section, \
         patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_and_send_daily_meetings_summaries()

    mock_section.assert_not_called()
    mock_send.assert_not_called()


def test_due_and_not_yet_sent_today_sends_the_summary(db_path, make_user):
    user_id = make_user(whatsapp_number="972500000001", display_name="Yossi")
    set_daily_meetings_summary_enabled(user_id, True)
    past_time = (datetime.now(TZ) - timedelta(minutes=5)).strftime("%H:%M")
    set_daily_meetings_summary_time(user_id, past_time)
    # last sent a while ago (not today) - due for a fresh send
    _set_last_sent_date(user_id, "2020-01-01")

    with patch("src.morning_brief._calendar_section", return_value="📅 היום ביומן:\n- 10:00 פגישה") as mock_section, \
         patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_and_send_daily_meetings_summaries()

    mock_section.assert_called_once_with(user_id, "Asia/Jerusalem")
    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs["to"] == "972500000001"
    assert "Yossi" in kwargs["body"]
    assert "פגישה" in kwargs["body"]


def test_a_second_poll_the_same_day_does_not_resend(db_path, make_user):
    """Simulates the real interval-polling behaviour: due once, sent once,
    a later poll within the same day must not send a second copy."""
    user_id = make_user(whatsapp_number="972500000001")
    set_daily_meetings_summary_enabled(user_id, True)
    past_time = (datetime.now(TZ) - timedelta(minutes=5)).strftime("%H:%M")
    set_daily_meetings_summary_time(user_id, past_time)
    _set_last_sent_date(user_id, "2020-01-01")

    with patch("src.morning_brief._calendar_section", return_value="📅 סיכום"), \
         patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_and_send_daily_meetings_summaries()  # first poll: due, sends
        check_and_send_daily_meetings_summaries()  # second poll, same day: should not resend

    mock_send.assert_called_once()


def test_not_connected_sends_a_sync_failed_message_with_the_reconnect_link(db_path, make_user):
    """Fails on BOTH the first attempt and the 2026-09-17 retry, so this is
    a genuine, repeated failure - the case that should still alert."""
    user_id = make_user(whatsapp_number="972500000001")
    set_daily_meetings_summary_enabled(user_id, True)
    past_time = (datetime.now(TZ) - timedelta(minutes=5)).strftime("%H:%M")
    set_daily_meetings_summary_time(user_id, past_time)
    _set_last_sent_date(user_id, "2020-01-01")

    with patch("src.morning_brief._calendar_section", side_effect=NotConnectedError()), \
         patch("src.integrations.google_oauth.build_auth_url", return_value="https://assistant.your-domain.example/oauth/start?state=abc"), \
         patch("src.scheduler.time.sleep"), \
         patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_and_send_daily_meetings_summaries()

    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs["to"] == "972500000001"
    assert "סנכרון יומן גוגל נכשל" in kwargs["body"]
    assert "https://assistant.your-domain.example/oauth/start?state=abc" in kwargs["body"]


def test_a_sync_failed_alert_is_also_marked_as_sent_so_it_does_not_repeat_every_poll(db_path, make_user):
    user_id = make_user(whatsapp_number="972500000001")
    set_daily_meetings_summary_enabled(user_id, True)
    past_time = (datetime.now(TZ) - timedelta(minutes=5)).strftime("%H:%M")
    set_daily_meetings_summary_time(user_id, past_time)
    _set_last_sent_date(user_id, "2020-01-01")

    with patch("src.morning_brief._calendar_section", side_effect=NotConnectedError()), \
         patch("src.integrations.google_oauth.build_auth_url", return_value="https://assistant.your-domain.example/oauth/start?state=abc"), \
         patch("src.scheduler.time.sleep"), \
         patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_and_send_daily_meetings_summaries()  # first poll: alerts once
        check_and_send_daily_meetings_summaries()  # second poll, same day: must not alert again

    mock_send.assert_called_once()


def test_expired_token_also_sends_the_reconnect_link(db_path, make_user):
    """Same treatment for a dead/expired token as for never having connected
    at all - also fails on both the first attempt and the retry."""
    user_id = make_user(whatsapp_number="972500000001")
    set_daily_meetings_summary_enabled(user_id, True)
    past_time = (datetime.now(TZ) - timedelta(minutes=5)).strftime("%H:%M")
    set_daily_meetings_summary_time(user_id, past_time)
    _set_last_sent_date(user_id, "2020-01-01")

    with patch("src.morning_brief._calendar_section", side_effect=GoogleAuthExpiredError()), \
         patch("src.integrations.google_oauth.build_auth_url", return_value="https://assistant.your-domain.example/oauth/start?state=xyz"), \
         patch("src.scheduler.time.sleep"), \
         patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_and_send_daily_meetings_summaries()

    mock_send.assert_called_once()
    assert "https://assistant.your-domain.example/oauth/start?state=xyz" in mock_send.call_args.kwargs["body"]


def test_a_transient_failure_that_recovers_on_retry_does_not_alert(db_path, make_user):
    """2026-09-17 fix: found live that a single transient Google API hiccup
    (not a real dead connection - a manual re-check moments later succeeded
    cleanly) was enough to send the scary "you need to reconnect" message.
    The retry means a failure that clears up on the second attempt should
    just send the real summary, with no reconnect message at all."""
    user_id = make_user(whatsapp_number="972500000001", display_name="Yossi")
    set_daily_meetings_summary_enabled(user_id, True)
    past_time = (datetime.now(TZ) - timedelta(minutes=5)).strftime("%H:%M")
    set_daily_meetings_summary_time(user_id, past_time)
    _set_last_sent_date(user_id, "2020-01-01")

    with patch(
        "src.morning_brief._calendar_section",
        side_effect=[NotConnectedError(), "📅 היום ביומן:\n- 10:00 פגישה"],
    ), patch("src.scheduler.time.sleep") as mock_sleep, \
         patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_and_send_daily_meetings_summaries()

    mock_sleep.assert_called_once()  # the retry actually paused before trying again
    mock_send.assert_called_once()
    body = mock_send.call_args.kwargs["body"]
    assert "סנכרון יומן גוגל נכשל" not in body
    assert "פגישה" in body


def test_a_non_auth_failure_is_not_marked_as_sent_so_it_retries_at_the_next_poll(db_path, make_user):
    """A transient, non-auth failure (_calendar_section returns None,
    swallowed internally) should keep retrying at the NEXT poll (minutes
    later), not wait a full day like the old fixed-cron design did."""
    user_id = make_user(whatsapp_number="972500000001")
    set_daily_meetings_summary_enabled(user_id, True)
    past_time = (datetime.now(TZ) - timedelta(minutes=5)).strftime("%H:%M")
    set_daily_meetings_summary_time(user_id, past_time)
    _set_last_sent_date(user_id, "2020-01-01")

    with patch("src.morning_brief._calendar_section", return_value=None), \
         patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_and_send_daily_meetings_summaries()
    mock_send.assert_not_called()

    # simulate the very next poll a few minutes later - recovered this time
    with patch("src.morning_brief._calendar_section", return_value="📅 סיכום"), \
         patch("src.integrations.whatsapp.send_text_message") as mock_send2:
        check_and_send_daily_meetings_summaries()
    mock_send2.assert_called_once()  # not blocked by a stale "already handled today" marker


def test_one_user_failing_does_not_block_the_next(db_path, make_user):
    """Per-user error isolation, same 12.3 principle as every other proactive job here."""
    user_a = make_user(whatsapp_number="972500000001")
    user_b = make_user(whatsapp_number="972500000002")
    for uid in (user_a, user_b):
        set_daily_meetings_summary_enabled(uid, True)
        past_time = (datetime.now(TZ) - timedelta(minutes=5)).strftime("%H:%M")
        set_daily_meetings_summary_time(uid, past_time)
        _set_last_sent_date(uid, "2020-01-01")

    with patch("src.morning_brief._calendar_section", return_value="📅 סיכום"), \
         patch("src.integrations.whatsapp.send_text_message", side_effect=[Exception("boom"), None]) as mock_send:
        check_and_send_daily_meetings_summaries()

    assert mock_send.call_count == 2

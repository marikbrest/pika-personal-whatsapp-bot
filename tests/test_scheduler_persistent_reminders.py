"""
check_and_send_persistent_reminders (new feature, 2026-09-17) - the
proactive re-nag + escalation-to-owner side of a nagging reminder. See
tests/test_persistent_reminders_models.py for the storage layer and
tests/test_tool_batch14.py for the chat-driven create/list/cancel tool that
populates it.
"""
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.db.models import (
    get_connection,
    get_due_persistent_reminders,
    save_contact,
    save_persistent_reminder,
)
from src.scheduler import check_and_send_persistent_reminders

NOW = datetime.now(ZoneInfo("UTC"))
PAST = NOW - timedelta(minutes=1)


def test_sends_nothing_when_nothing_is_due(db_path, make_user):
    owner_id = make_user()
    contact_id = save_contact(owner_id, "דני", "972500000071")
    save_persistent_reminder(owner_id, contact_id, "X", NOW + timedelta(hours=1))

    with patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_and_send_persistent_reminders()
    mock_send.assert_not_called()


def test_nag_passes_kid_facing_role_not_display_name_to_the_creative_text_generator(db_path, make_user):
    """2026-09-25: the nag text (generated via Gemini) must be told the
    parent's kid-facing role ("אבא"/"אימא"), not their real display_name -
    same split as the ordinary-reminder prefix."""
    from src.db.models import get_connection

    owner_id = make_user(whatsapp_number="972500000001", display_name="יוסי")
    conn = get_connection()
    try:
        conn.execute("UPDATE users SET kid_facing_role = ? WHERE id = ?", ("אבא", owner_id))
        conn.commit()
    finally:
        conn.close()
    contact_id = save_contact(owner_id, "דני", "972500000071")
    save_persistent_reminder(owner_id, contact_id, "שיעורי בית", PAST)

    with patch("src.scheduler._generate_creative_reminder_text", return_value="x") as mock_generate, \
         patch("src.integrations.whatsapp.send_text_message"):
        check_and_send_persistent_reminders()

    mock_generate.assert_called_once_with("שיעורי בית", "דני", "אבא")


def test_due_and_under_max_attempts_nags_the_recipient_and_reschedules(db_path, make_user):
    owner_id = make_user(whatsapp_number="972500000001", display_name="Yossi")
    contact_id = save_contact(owner_id, "דני", "972500000071")
    save_persistent_reminder(owner_id, contact_id, "שיעורי בית", PAST)

    with patch("src.integrations.gemini.call_gemini_json", return_value={"message": "נו דני, מה קורה עם שיעורי בית? 😅"}), \
         patch("src.integrations.whatsapp.send_text_or_template", return_value=True) as mock_send:
        check_and_send_persistent_reminders()

    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs["to"] == "972500000071"  # the RECIPIENT, not the owner
    assert "שיעורי בית" in kwargs["body"]
    assert 'תגיד לי "עשיתי"' in kwargs["body"]  # the fixed confirmation instruction is always appended

    # rescheduled ~5 minutes out, not due right now anymore
    assert get_due_persistent_reminders(NOW.isoformat()) == []
    assert len(get_due_persistent_reminders((NOW + timedelta(minutes=6)).isoformat())) == 1


def test_nag_goes_through_the_24h_window_template_fallback(db_path, make_user):
    """2026-09-25: a kid is outside WhatsApp's 24h window by default (a
    delivered/read receipt does not open it - only a message they actually
    send does), so the nag MUST go through send_text_or_template. Before
    this it used plain send_text_message and simply vanished, silently,
    for exactly the recipients this whole feature exists for."""
    owner_id = make_user(whatsapp_number="972500000001", display_name="יוסי")
    contact_id = save_contact(owner_id, "דני", "972500000071")
    save_persistent_reminder(owner_id, contact_id, "שיעורי בית", PAST)

    with patch("src.integrations.gemini.call_gemini_json", return_value={"message": "נו דני?\nמה קורה? 😅"}), \
         patch("src.integrations.whatsapp.send_text_or_template", return_value=True) as mock_send:
        check_and_send_persistent_reminders()

    kwargs = mock_send.call_args.kwargs
    assert kwargs["template_name"] == "reminder_notification"
    assert kwargs["language_code"] == "he"
    # the template slot carries a plain one-liner, never the creative body -
    # WhatsApp rejects template parameters containing newlines, and the
    # creative body always has them (the confirmation line is appended).
    assert len(kwargs["body_params"]) == 1
    assert "\n" not in kwargs["body_params"][0]
    assert "שיעורי בית" in kwargs["body_params"][0]


def test_a_nag_that_fails_outright_notifies_both_parents(db_path, make_user):
    owner_id = make_user(whatsapp_number="972500000001", display_name="יוסי")
    other_parent_id = make_user(whatsapp_number="972500000002", display_name="רונית")
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE users SET notify_on_reminder_delivery_failure = 1 WHERE id = ?", (other_parent_id,)
        )
        conn.commit()
    finally:
        conn.close()
    contact_id = save_contact(owner_id, "דני", "972500000071")
    save_persistent_reminder(owner_id, contact_id, "שיעורי בית", PAST)

    with patch("src.integrations.gemini.call_gemini_json", return_value={"message": "נו?"}), \
         patch("src.integrations.whatsapp.send_text_or_template", return_value=False), \
         patch("src.integrations.whatsapp.send_text_message") as mock_notify:
        check_and_send_persistent_reminders()

    notified = {call.kwargs["to"] for call in mock_notify.call_args_list}
    assert notified == {"972500000001", "972500000002"}
    assert "דני" in mock_notify.call_args_list[0].kwargs["body"]


def test_async_failure_callbacks_are_bound_per_row_not_captured_by_closure(db_path, make_user):
    """2026-09-25 regression: the on_permanent_failure lambda is invoked
    long after this loop has finished (from whatever later webhook carries
    the failed status). Closing over the loop variables instead of binding
    them would make EVERY late failure report whichever kid happened to be
    last in the loop."""
    owner_id = make_user(whatsapp_number="972500000001", display_name="יוסי")
    ido_contact = save_contact(owner_id, "דני", "972500000071")
    tomer_contact = save_contact(owner_id, "תומר", "972500000073")
    save_persistent_reminder(owner_id, ido_contact, "שיעורי בית", PAST)
    save_persistent_reminder(owner_id, tomer_contact, "לסדר חדר", PAST)

    captured = []
    with patch("src.integrations.gemini.call_gemini_json", return_value={"message": "נו?"}), \
         patch("src.integrations.whatsapp.send_text_or_template",
               side_effect=lambda **kw: captured.append(kw["on_permanent_failure"]) or True):
        check_and_send_persistent_reminders()

    assert len(captured) == 2
    # invoke both callbacks AFTER the loop is done - each must still report
    # its own recipient, not the last one processed
    with patch("src.scheduler.notify_reminder_delivery_failure") as mock_notify:
        for callback in captured:
            callback()

    reported = {call.args[0] for call in mock_notify.call_args_list}
    assert reported == {"דני", "תומר"}


def test_creative_text_falls_back_to_the_plain_template_on_any_gemini_failure(db_path, make_user):
    """2026-09-20: a nag that arrives on time with boring, static phrasing
    is far better than a nag that doesn't arrive at all because the
    creative-writing call failed - so any Gemini failure here must never
    block or corrupt the actual send."""
    owner_id = make_user(whatsapp_number="972500000001", display_name="Yossi")
    contact_id = save_contact(owner_id, "דני", "972500000071")
    save_persistent_reminder(owner_id, contact_id, "שיעורי בית", PAST)

    with patch("src.integrations.gemini.call_gemini_json", side_effect=Exception("boom")), \
         patch("src.integrations.whatsapp.send_text_or_template", return_value=True) as mock_send:
        check_and_send_persistent_reminders()

    mock_send.assert_called_once()
    body = mock_send.call_args.kwargs["body"]
    assert "שיעורי בית" in body
    assert "Yossi" in body
    assert 'תגיד לי "עשיתי"' in body


def test_repeated_polls_keep_nagging_until_max_attempts_then_escalate(db_path, make_user):
    """
    Simulates 4 successive polling cycles (nag, nag, nag, then escalate once
    max_attempts=3 is reached) by directly resetting next_trigger_at to the
    past between calls - the real scheduler.check_and_send_persistent_
    reminders always reschedules 5 minutes into the future on each real
    call, so a test that wants to exercise several cycles without actually
    waiting has to fast-forward that clock itself, same technique
    test_scheduler_daily_meetings_summary.py's _set_last_sent_date uses for
    its own "simulate a later poll" cases.
    """
    from src.db.models import get_connection

    owner_id = make_user(whatsapp_number="972500000001", display_name="Yossi")
    contact_id = save_contact(owner_id, "דני", "972500000071")
    rid = save_persistent_reminder(owner_id, contact_id, "שיעורי בית", PAST, retry_interval_minutes=5, max_attempts=3)

    def force_due():
        conn = get_connection()
        try:
            conn.execute("UPDATE persistent_reminders SET next_trigger_at = ? WHERE id = ?", (PAST.isoformat(), rid))
            conn.commit()
        finally:
            conn.close()

    # nags to the kid go through send_text_or_template (24h-window
    # fallback); the escalation to the parent is a plain text send, since
    # the parent is always inside the window - they talk to the bot.
    nagged = []
    escalated = []
    with patch("src.integrations.gemini.call_gemini_json", return_value={"message": "נו דני, מה קורה?"}), \
         patch("src.integrations.whatsapp.send_text_or_template",
               side_effect=lambda **kw: nagged.append(kw["to"]) or True), \
         patch("src.integrations.whatsapp.send_text_message",
               side_effect=lambda to, body: escalated.append((to, body))):
        check_and_send_persistent_reminders()  # attempt 1/3 -> nag
        force_due()
        check_and_send_persistent_reminders()  # attempt 2/3 -> nag
        force_due()
        check_and_send_persistent_reminders()  # attempt 3/3 -> nag
        force_due()
        check_and_send_persistent_reminders()  # already at max_attempts -> escalate instead

    assert nagged == ["972500000071", "972500000071", "972500000071"]
    assert len(escalated) == 1
    assert escalated[0][0] == "972500000001"  # the parent
    assert "שיעורי בית" in escalated[0][1]


def test_confirmed_reminder_is_never_nagged_again(db_path, make_user):
    from src.db.models import mark_persistent_reminder_done

    owner_id = make_user(whatsapp_number="972500000001")
    contact_id = save_contact(owner_id, "דני", "972500000071")
    rid = save_persistent_reminder(owner_id, contact_id, "שיעורי בית", PAST)
    mark_persistent_reminder_done(rid)

    with patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_and_send_persistent_reminders()
    mock_send.assert_not_called()


def test_a_recurring_reminder_resets_instead_of_terminally_escalating(db_path, make_user):
    """A daily/weekly reminder that hits max_attempts still gets the owner
    told, but resets itself for the next occurrence instead of dying
    forever - one unconfirmed day should not kill tomorrow's cycle."""
    from src.db.models import get_connection, list_active_persistent_reminders

    owner_id = make_user(whatsapp_number="972500000001", timezone="Asia/Jerusalem")
    contact_id = save_contact(owner_id, "דני", "972500000071")
    rid = save_persistent_reminder(
        owner_id, contact_id, "להאכיל את הכלב", PAST,
        retry_interval_minutes=5, max_attempts=1, schedule_type="daily", schedule_time="20:00",
    )

    def force_due():
        conn = get_connection()
        try:
            conn.execute("UPDATE persistent_reminders SET next_trigger_at = ? WHERE id = ?", (PAST.isoformat(), rid))
            conn.commit()
        finally:
            conn.close()

    with patch("src.integrations.gemini.call_gemini_json", return_value={"message": "נו דני, מה קורה עם הכלב?"}), \
         patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_and_send_persistent_reminders()  # attempt 1/1 -> nag
        force_due()
        check_and_send_persistent_reminders()  # already at max_attempts -> escalate + reset

    assert mock_send.call_count == 2
    # the row is still active (pending), not terminally escalated
    rows = list_active_persistent_reminders(owner_id)
    assert len(rows) == 1
    assert rows[0]["attempts_sent"] == 0
    # not due right now - pushed out to the next daily occurrence
    assert get_due_persistent_reminders(NOW.isoformat()) == []


def test_one_reminder_failing_does_not_block_the_next(db_path, make_user):
    """Per-row error isolation, same 12.3 principle as every other proactive job here."""
    owner_a = make_user(whatsapp_number="972500000001")
    owner_b = make_user(whatsapp_number="972500000002")
    contact_a = save_contact(owner_a, "דני", "972500000071")
    contact_b = save_contact(owner_b, "תומר", "972500000073")
    save_persistent_reminder(owner_a, contact_a, "X", PAST)
    save_persistent_reminder(owner_b, contact_b, "Y", PAST)

    with patch("src.integrations.gemini.call_gemini_json", return_value={"message": "נו, מה קורה?"}), \
         patch("src.integrations.whatsapp.send_text_message", side_effect=[Exception("boom"), None]) as mock_send:
        check_and_send_persistent_reminders()

    assert mock_send.call_count == 2

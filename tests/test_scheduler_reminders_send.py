"""
check_and_send_reminders' actual delivery path - as opposed to the pure
compute_next_trigger date math in test_scheduler_reminders.py. Covers the
2026-09-13 template-fallback wiring: reminder_notification is now tried via
send_text_or_template (the 24h-window fallback), in place of a plain
send_text_message call.
"""
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.db import models
from src.scheduler import check_and_send_reminders

NOW = datetime.now(ZoneInfo("UTC"))
_DUE = (NOW - timedelta(minutes=1)).isoformat()


def _seed_reminder(user_id, content, recipient_contact_id=None):
    conn = models.get_connection()
    try:
        conn.execute(
            "INSERT INTO reminders (user_id, content, schedule_type, schedule_time, schedule_days, "
            "next_trigger_at, recipient_contact_id) VALUES (?, ?, 'once', '09:00', NULL, ?, ?)",
            (user_id, content, _DUE, recipient_contact_id),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_contact(owner_user_id, name, whatsapp_number):
    conn = models.get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO contacts (owner_user_id, name, whatsapp_number) VALUES (?, ?, ?)",
            (owner_user_id, name, whatsapp_number),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _set_kid_facing_role(user_id: int, role: str) -> None:
    conn = models.get_connection()
    try:
        conn.execute("UPDATE users SET kid_facing_role = ? WHERE id = ?", (role, user_id))
        conn.commit()
    finally:
        conn.close()


def test_kid_facing_reminder_prefix_uses_kid_facing_role_not_display_name(db_path, make_user):
    """2026-09-25: Yossi asked for a split - a message TO a kid should say
    "אבא"/"אימא", never the parent's own name, even though the bot still
    addresses the parent themselves by their real name everywhere else."""
    owner_id = make_user(whatsapp_number="972500000001", display_name="יוסי")
    _set_kid_facing_role(owner_id, "אבא")
    contact_id = _seed_contact(owner_id, "דני", "972500000099")
    _seed_reminder(owner_id, "לקנות חלב", recipient_contact_id=contact_id)

    with patch("src.integrations.whatsapp.send_text_or_template", return_value=True) as mock_send:
        check_and_send_reminders()

    body = mock_send.call_args.kwargs["body"]
    assert "מ-אבא" in body
    assert "יוסי" not in body


def test_kid_facing_reminder_prefix_falls_back_to_display_name_when_role_unset(db_path, make_user):
    """A user with no kid_facing_role (everyone except Yossi/Ronit) keeps
    the old behavior - their real display_name in the prefix."""
    owner_id = make_user(whatsapp_number="972500000001", display_name="Gil")
    contact_id = _seed_contact(owner_id, "דני", "972500000099")
    _seed_reminder(owner_id, "לקנות חלב", recipient_contact_id=contact_id)

    with patch("src.integrations.whatsapp.send_text_or_template", return_value=True) as mock_send:
        check_and_send_reminders()

    assert "מ-Gil" in mock_send.call_args.kwargs["body"]


def test_due_reminder_sent_via_reminder_notification_template_fallback(db_path, make_user):
    user_id = make_user(whatsapp_number="972500000001")
    _seed_reminder(user_id, "לקחת תרופה")

    with patch("src.integrations.whatsapp.send_text_or_template", return_value=True) as mock_send:
        check_and_send_reminders()

    mock_send.assert_called_once()
    kwargs = mock_send.call_args.kwargs
    assert kwargs["to"] == "972500000001"
    assert kwargs["template_name"] == "reminder_notification"
    assert kwargs["language_code"] == "he"
    assert kwargs["body_params"] == ["לקחת תרופה"]
    assert "לקחת תרופה" in kwargs["body"]

    assert models.list_active_reminders(user_id) == []  # "once" deactivated after a successful send


def test_multiple_due_reminders_are_consolidated_into_one_template_param(db_path, make_user):
    user_id = make_user(whatsapp_number="972500000001")
    _seed_reminder(user_id, "לקחת תרופה")
    _seed_reminder(user_id, "להתקשר לרופא")

    with patch("src.integrations.whatsapp.send_text_or_template", return_value=True) as mock_send:
        check_and_send_reminders()

    mock_send.assert_called_once()
    body_params = mock_send.call_args.kwargs["body_params"]
    assert len(body_params) == 1  # the template has exactly one {{1}} slot
    assert "לקחת תרופה" in body_params[0]
    assert "להתקשר לרופא" in body_params[0]


def test_contact_reminder_still_notifies_owner_when_the_template_fallback_also_fails(db_path, make_user):
    """Regression for the pre-existing behavior: when even the template
    fallback fails (not yet approved by Meta, or a genuinely bad number), the
    owner must still be told and the reminder must still be retired -
    unchanged from before templates existed."""
    user_id = make_user(whatsapp_number="972500000001", display_name="יוסי")
    contact_id = _seed_contact(user_id, "רונית", "972500000099")
    _seed_reminder(user_id, "לקנות חלב", recipient_contact_id=contact_id)

    with patch("src.integrations.whatsapp.send_text_or_template", return_value=False), \
         patch("src.integrations.whatsapp.send_text_message") as mock_owner_send:
        check_and_send_reminders()

    mock_owner_send.assert_called_once()
    assert mock_owner_send.call_args.kwargs["to"] == "972500000001"
    assert "רונית" in mock_owner_send.call_args.kwargs["body"]

    assert models.list_active_reminders(user_id) == []  # deactivated even though delivery ultimately failed


def test_list_reminder_delivery_failure_notification_numbers_only_returns_opted_in_active_users(db_path, make_user):
    opted_in_id = make_user(whatsapp_number="972500000001")
    make_user(whatsapp_number="972500000002")  # not opted in
    opted_in_but_inactive_id = make_user(whatsapp_number="972500000003", is_active=False)
    _set_notify_on_delivery_failure(opted_in_id)
    _set_notify_on_delivery_failure(opted_in_but_inactive_id)

    numbers = models.list_reminder_delivery_failure_notification_numbers()
    assert numbers == ["972500000001"]


def _set_notify_on_delivery_failure(user_id: int) -> None:
    conn = models.get_connection()
    try:
        conn.execute("UPDATE users SET notify_on_reminder_delivery_failure = 1 WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def test_sync_delivery_failure_also_notifies_the_other_opted_in_parent(db_path, make_user):
    """2026-09-19: both parents should hear about a genuine delivery
    failure to a kid, not just whichever of them created the reminder -
    Yossi's own request, made the same day the async 24h-window fallback
    fix (below) shipped."""
    owner_id = make_user(whatsapp_number="972500000001", display_name="יוסי")
    other_parent_id = make_user(whatsapp_number="972500000002", display_name="רונית")
    _set_notify_on_delivery_failure(other_parent_id)
    contact_id = _seed_contact(owner_id, "דני", "972500000099")
    _seed_reminder(owner_id, "לקנות חלב", recipient_contact_id=contact_id)

    with patch("src.integrations.whatsapp.send_text_or_template", return_value=False), \
         patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_and_send_reminders()

    notified_numbers = {call.kwargs["to"] for call in mock_send.call_args_list}
    assert notified_numbers == {"972500000001", "972500000002"}


def test_sync_delivery_failure_does_not_double_notify_when_owner_is_the_opted_in_parent(db_path, make_user):
    owner_id = make_user(whatsapp_number="972500000001", display_name="יוסי")
    _set_notify_on_delivery_failure(owner_id)
    contact_id = _seed_contact(owner_id, "דני", "972500000099")
    _seed_reminder(owner_id, "לקנות חלב", recipient_contact_id=contact_id)

    with patch("src.integrations.whatsapp.send_text_or_template", return_value=False), \
         patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_and_send_reminders()

    assert mock_send.call_count == 1  # not sent twice to the same number


def test_async_permanent_failure_notifies_both_parents(db_path, make_user):
    """The real bug this closes the loop on: the initial send "succeeds"
    (200 OK), the 24h-window rejection surfaces later via the status
    webhook, the template retry is attempted - and if THAT also fails,
    both parents must still hear about it, even though
    check_and_send_reminders' own call has long since returned."""
    from src.integrations import whatsapp

    owner_id = make_user(whatsapp_number="972500000001", display_name="יוסי")
    other_parent_id = make_user(whatsapp_number="972500000002", display_name="רונית")
    _set_notify_on_delivery_failure(other_parent_id)
    contact_id = _seed_contact(owner_id, "דני", "972500000099")
    _seed_reminder(owner_id, "לקנות חלב", recipient_contact_id=contact_id)

    whatsapp._pending_24h_window_fallback.clear()
    with patch.object(whatsapp, "_post_message", return_value=(True, {"messages": [{"id": "wamid.XYZ"}]})):
        check_and_send_reminders()

    assert "wamid.XYZ" in whatsapp._pending_24h_window_fallback

    with patch.object(whatsapp, "send_template_message", return_value=False), \
         patch("src.integrations.whatsapp.send_text_message") as mock_send:
        whatsapp.handle_delivery_status("wamid.XYZ", "failed", [{"code": 131047}])

    notified_numbers = {call.kwargs["to"] for call in mock_send.call_args_list}
    assert notified_numbers == {"972500000001", "972500000002"}
    whatsapp._pending_24h_window_fallback.clear()


def test_async_retry_success_does_not_trigger_any_failure_notification(db_path, make_user):
    from src.integrations import whatsapp

    owner_id = make_user(whatsapp_number="972500000001", display_name="יוסי")
    contact_id = _seed_contact(owner_id, "דני", "972500000099")
    _seed_reminder(owner_id, "לקנות חלב", recipient_contact_id=contact_id)

    whatsapp._pending_24h_window_fallback.clear()
    with patch.object(whatsapp, "_post_message", return_value=(True, {"messages": [{"id": "wamid.OK"}]})):
        check_and_send_reminders()

    with patch.object(whatsapp, "send_template_message", return_value=True), \
         patch("src.integrations.whatsapp.send_text_message") as mock_send:
        whatsapp.handle_delivery_status("wamid.OK", "failed", [{"code": 131047}])

    mock_send.assert_not_called()
    whatsapp._pending_24h_window_fallback.clear()

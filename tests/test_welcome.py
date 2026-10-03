"""One-time privacy/terms welcome for newly added users (src/welcome.py)."""
from unittest.mock import patch

from src import welcome
from src.db import models


def _welcome_sent_at(user_id):
    conn = models.get_connection()
    try:
        return conn.execute("SELECT welcome_sent_at FROM users WHERE id = ?", (user_id,)).fetchone()[0]
    finally:
        conn.close()


def test_sends_once_with_the_policy_links_and_the_template_fallback(make_user):
    uid = make_user(whatsapp_number="972500000021")
    with patch.object(welcome, "PUBLIC_BASE_URL", "https://bot.example.org"), \
         patch.object(welcome, "send_text_or_template", return_value=True) as send:
        assert welcome.send_welcome_if_needed(uid) == "sent"
        assert welcome.send_welcome_if_needed(uid) == "already_sent"

    send.assert_called_once()
    kw = send.call_args.kwargs
    assert kw["to"] == "972500000021"
    assert "https://bot.example.org/privacy" in kw["body"] and "https://bot.example.org/terms" in kw["body"]
    assert "Gemini" in kw["body"] and "יכול טכנית לקרוא" in kw["body"]
    assert kw["template_name"] == "welcome_user"
    assert kw["body_params"] == ["https://bot.example.org/privacy", "https://bot.example.org/terms"]
    assert _welcome_sent_at(uid) is not None


def test_skipped_without_public_base_url(make_user):
    uid = make_user(whatsapp_number="972500000021")
    with patch.object(welcome, "PUBLIC_BASE_URL", ""), patch.object(welcome, "send_text_or_template") as send:
        assert welcome.send_welcome_if_needed(uid) == "skipped_unconfigured"
    send.assert_not_called()
    assert _welcome_sent_at(uid) is None


def test_a_failed_send_is_not_recorded_so_it_can_be_retried(make_user):
    uid = make_user(whatsapp_number="972500000021")
    with patch.object(welcome, "PUBLIC_BASE_URL", "https://bot.example.org"), \
         patch.object(welcome, "send_text_or_template", return_value=False):
        assert welcome.send_welcome_if_needed(uid) == "failed"
    assert _welcome_sent_at(uid) is None


def test_an_exception_never_escapes(make_user):
    uid = make_user(whatsapp_number="972500000021")
    with patch.object(welcome, "PUBLIC_BASE_URL", "https://bot.example.org"), \
         patch.object(welcome, "send_text_or_template", side_effect=RuntimeError("boom")):
        assert welcome.send_welcome_if_needed(uid) == "failed"
    assert welcome.send_welcome_if_needed(99999) in ("no_such_user", "skipped_unconfigured")


def test_adding_a_user_from_chat_sends_the_welcome(make_user):
    from src.webhook_handler import _handle_user_manage

    admin_id = make_user(whatsapp_number="972500000020", is_admin=True)
    admin = models.get_user_by_whatsapp_number("972500000020")
    assert admin_id
    with patch("src.welcome.send_welcome_if_needed") as send:
        reply = _handle_user_manage(admin, {"action": "add", "whatsapp_number": "972500000031", "display_name": "Dad"})
    assert "יכול עכשיו להשתמש בבוט" in reply
    send.assert_called_once()


def test_adding_a_user_from_the_dashboard_sends_the_welcome(client, make_user):
    with patch("src.admin_handler._authorized", return_value=True), \
         patch("src.admin_handler._admin_email", return_value="a@example.test"), \
         patch("src.welcome.send_welcome_if_needed") as send:
        r = client.post("/admin/users/add", data={"display_name": "Mom", "whatsapp_number": "0501234599"}, follow_redirects=False)
    assert r.status_code == 303
    send.assert_called_once()

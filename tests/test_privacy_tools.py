"""
explain_privacy / manage_my_data (2026-09-26) - the "does the admin see my
messages" answer, grounded in fixed text (never a free-form Gemini guess -
same principle as _handle_explain_capabilities), plus the self-service
"show me what you have on me" / "delete my history" tool it points users
toward.
"""
from src.db.models import (
    delete_all_messages_for_user,
    get_user_data_summary,
    save_incoming_message,
    save_outgoing_message,
)
from src.webhook_handler import _handle_explain_privacy, _handle_manage_my_data


def _user(user_id=1):
    return {"id": user_id, "timezone": "Asia/Jerusalem", "is_admin": False}


# ===== explain_privacy =====

def test_explain_privacy_states_the_admin_does_not_see_message_content():
    reply = _handle_explain_privacy()
    assert "לא רואה" in reply
    assert "תוכן ההודעות" in reply or "מיילים" in reply


def test_explain_privacy_mentions_the_audit_log():
    reply = _handle_explain_privacy()
    assert "לוג ביקורת" in reply


def test_explain_privacy_points_to_the_self_service_data_tool():
    reply = _handle_explain_privacy()
    assert "מה יש עליי" in reply
    assert "תמחק את ההיסטוריה שלי" in reply


def test_explain_privacy_discloses_proactive_mode_reads_gmail_and_calendar():
    """Updated 2026-09-27 for the Context-Aware Gatekeeper: proactive mode
    reads Gmail/Calendar content autonomously in the background - a
    materially different privacy posture than "only reads what you ask
    about", so it must be disclosed here, not just implemented."""
    reply = _handle_explain_privacy()
    assert "המצב היזום" in reply
    assert "מיילים" in reply and "יומן" in reply
    assert "כבוי כברירת מחדל" in reply


# ===== manage_my_data: show =====

def test_manage_my_data_show_reports_real_counts(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי")
    save_incoming_message(1, "היי", "wamid.1", "text")
    save_outgoing_message(1, "שלום!")

    reply = _handle_manage_my_data(_user(), {"action": "show"})

    assert "2" in reply  # message_count
    assert "Google" in reply


def test_manage_my_data_show_reflects_google_connection_status(db_path, make_user):
    from src.db.models import get_connection

    make_user(whatsapp_number="972500000001", display_name="יוסי")
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO oauth_tokens (user_id, provider, access_token_encrypted, refresh_token_encrypted, scope) "
            "VALUES (1, 'google', 'x', 'y', 'z')"
        )
        conn.commit()
    finally:
        conn.close()

    reply = _handle_manage_my_data(_user(), {"action": "show"})
    assert "מחובר" in reply
    assert "לא מחובר" not in reply


def test_manage_my_data_show_only_counts_the_requesting_users_own_data(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי")
    make_user(whatsapp_number="972500000002", display_name="רונית")
    save_incoming_message(2, "הודעה של רונית", "wamid.2", "text")

    summary = get_user_data_summary(1)
    assert summary["message_count"] == 0


# ===== manage_my_data: delete_history =====

def test_manage_my_data_delete_history_removes_only_this_users_messages(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי")
    make_user(whatsapp_number="972500000002", display_name="רונית")
    save_incoming_message(1, "הודעה של יוסי", "wamid.1", "text")
    save_incoming_message(2, "הודעה של רונית", "wamid.2", "text")

    reply = _handle_manage_my_data(_user(1), {"action": "delete_history"})

    assert "1" in reply
    assert get_user_data_summary(1)["message_count"] == 0
    assert get_user_data_summary(2)["message_count"] == 1  # untouched


def test_delete_all_messages_for_user_returns_the_deleted_count(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי")
    save_incoming_message(1, "א", "wamid.1", "text")
    save_incoming_message(1, "ב", "wamid.2", "text")

    assert delete_all_messages_for_user(1) == 2
    assert delete_all_messages_for_user(1) == 0  # already empty

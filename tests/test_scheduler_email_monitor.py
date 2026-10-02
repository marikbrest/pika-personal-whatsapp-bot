"""
check_and_monitor_new_emails (2026-09-27) - the Context-Aware Gatekeeper's
second collector. Mocks list_recent_emails/classify_new_emails (their own
correctness proven elsewhere), send_text_message, and
proactive.assess_situation (own correctness covered by
test_proactive_situation_assessment.py) - returning None makes
assess_and_deliver fall back to this collector's own plain factual
message, avoiding a real (non-deterministic) Gemini call in these tests.
Real DB fixtures for proactive_settings/gmail_seen_messages state.
"""
import pytest
from unittest.mock import patch

from src.db.models import get_connection, set_proactive_enabled
from src.scheduler import check_and_monitor_new_emails

# Proactive delivery is quiet-hours-gated; never depend on the real time of day.
pytestmark = pytest.mark.usefixtures("daytime_clock")


def _email(id_, from_="parent@gan.example.com", subject="עדכון", snippet="תקציר"):
    return {"id": id_, "from": from_, "subject": subject, "snippet": snippet, "date": ""}


def _seen_ids(user_id):
    conn = get_connection()
    try:
        rows = conn.execute("SELECT message_id FROM gmail_seen_messages WHERE user_id = ?", (user_id,)).fetchall()
        return {r["message_id"] for r in rows}
    finally:
        conn.close()


def test_does_nothing_for_a_user_who_never_opted_in(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    with patch("src.integrations.gmail.list_recent_emails") as mock_list, \
         patch("src.integrations.whatsapp.send_text_or_template") as mock_send:
        check_and_monitor_new_emails()

    mock_list.assert_not_called()
    mock_send.assert_not_called()


def test_first_run_seeds_seen_messages_without_notifying(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    set_proactive_enabled(1, True)
    emails = [_email("m1")]

    with patch("src.integrations.gmail.list_recent_emails", return_value=emails), \
         patch("src.integrations.gmail.classify_new_emails") as mock_classify, \
         patch("src.integrations.whatsapp.send_text_or_template") as mock_send:
        check_and_monitor_new_emails()

    mock_classify.assert_not_called()  # first run - no classification call, no flood
    mock_send.assert_not_called()
    assert "m1" in _seen_ids(1)


def test_a_genuinely_new_urgent_email_after_first_run_is_reported(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    set_proactive_enabled(1, True)
    first = [_email("m1")]
    second = first + [_email("m2", from_="gan@example.com", subject="חשוב על דני")]

    with patch("src.integrations.gmail.list_recent_emails", return_value=first), \
         patch("src.integrations.whatsapp.send_text_or_template"):
        check_and_monitor_new_emails()  # seeds silently

    classification = [{"id": "m2", "category": "urgent_vip", "summary": "צריך לאסוף את דני"}]
    with patch("src.integrations.gmail.list_recent_emails", return_value=second), \
         patch("src.integrations.gmail.classify_new_emails", return_value=classification), \
         patch("src.proactive.assess_situation", return_value=None), \
         patch("src.integrations.whatsapp.send_text_or_template", return_value=True) as mock_send:
        check_and_monitor_new_emails()

    mock_send.assert_called_once()
    assert "לאסוף את דני" in mock_send.call_args.kwargs["body"]
    assert "m2" in _seen_ids(1)


def test_the_llm_assessment_can_suppress_an_urgent_looking_email(db_path, make_user):
    """The situation-assessment layer's whole point: even a category the
    fixed rule would always report (urgent_vip) still goes through a real
    judgment call - it can decide the timing/context doesn't warrant it."""
    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    set_proactive_enabled(1, True)
    first = [_email("m1")]
    second = first + [_email("m2", from_="gan@example.com", subject="עדכון שגרתי")]

    with patch("src.integrations.gmail.list_recent_emails", return_value=first), \
         patch("src.integrations.whatsapp.send_text_or_template"):
        check_and_monitor_new_emails()

    classification = [{"id": "m2", "category": "urgent_vip", "summary": "עדכון"}]
    with patch("src.integrations.gmail.list_recent_emails", return_value=second), \
         patch("src.integrations.gmail.classify_new_emails", return_value=classification), \
         patch("src.proactive.assess_situation", return_value={"interrupt": False, "message": ""}), \
         patch("src.integrations.whatsapp.send_text_or_template") as mock_send:
        check_and_monitor_new_emails()

    mock_send.assert_not_called()


def test_an_other_category_email_is_not_reported(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    set_proactive_enabled(1, True)

    with patch("src.integrations.gmail.list_recent_emails", return_value=[_email("m1")]), \
         patch("src.integrations.whatsapp.send_text_or_template"):
        check_and_monitor_new_emails()

    second = [_email("m1"), _email("m2", subject="ניוזלטר שבועי")]
    classification = [{"id": "m2", "category": "other", "summary": ""}]
    with patch("src.integrations.gmail.list_recent_emails", return_value=second), \
         patch("src.integrations.gmail.classify_new_emails", return_value=classification), \
         patch("src.integrations.whatsapp.send_text_or_template") as mock_send:
        check_and_monitor_new_emails()

    mock_send.assert_not_called()


def test_a_message_is_marked_seen_even_if_classification_later_fails(db_path, make_user):
    """A classification failure must not cause the same email to be
    reprocessed (and possibly double-notified) on the next poll."""
    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    set_proactive_enabled(1, True)

    with patch("src.integrations.gmail.list_recent_emails", return_value=[_email("m1")]), \
         patch("src.integrations.whatsapp.send_text_or_template"):
        check_and_monitor_new_emails()  # seeds

    second = [_email("m1"), _email("m2")]
    with patch("src.integrations.gmail.list_recent_emails", return_value=second), \
         patch("src.integrations.gmail.classify_new_emails", side_effect=RuntimeError("boom")):
        check_and_monitor_new_emails()  # must not raise

    assert "m2" in _seen_ids(1)


def test_a_broken_google_connection_for_one_user_does_not_block_others(db_path, make_user):
    from src.integrations.google_oauth import NotConnectedError

    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    make_user(whatsapp_number="972500000002", display_name="רונית", is_admin=False)
    set_proactive_enabled(1, True)
    set_proactive_enabled(2, True)

    def _list_recent(user_id, *a, **kw):
        if user_id == 1:
            raise NotConnectedError("not connected")
        return [_email("m1")]

    with patch("src.integrations.gmail.list_recent_emails", side_effect=_list_recent), \
         patch("src.integrations.whatsapp.send_text_or_template"):
        check_and_monitor_new_emails()  # must not raise

    assert "m1" in _seen_ids(2)

"""
Confirms the real wiring point in webhook_handler.py for the pilot-tool
cutover (2026-09-14, replaces the old Stage B shadow-wiring test now that
shadow logging is no longer part of the text-message path):
_classify_text_with_cutover is what actually decides the reply for text
messages now. Mocks it out entirely (its own correctness is
tests/test_tool_cutover.py's job) and checks the reply the mocked WhatsApp
send received is exactly what it returned.
"""
from unittest.mock import patch

from src.db import models

from .conftest import post_webhook, whatsapp_text_payload


def test_cutover_classifier_result_drives_the_real_reply(client, make_user):
    make_user(whatsapp_number="972500000001")
    fake_result = {"intent": "get_weather", "reply": "18 degrees and sunny"}

    with patch("src.webhook_handler._classify_text_with_cutover", return_value=fake_result), \
         patch("src.webhook_handler.send_text_message", return_value=True) as mock_send:
        post_webhook(client, whatsapp_text_payload("972500000001", "מה מזג האוויר"))

    mock_send.assert_called_once()
    assert mock_send.call_args.kwargs["body"] == "18 degrees and sunny"


def test_shadow_classification_is_no_longer_called_for_text_messages(client, make_user):
    """Regression guard: Stage B's shadow comparison call used to run
    alongside every text message - cutover replaced it, since
    _classify_text_with_cutover already makes the equivalent call itself. A
    second background call would just double the Gemini calls for nothing
    still left to compare."""
    make_user(whatsapp_number="972500000001")
    fake_result = {"intent": "chat", "reply": "hi"}

    with patch("src.webhook_handler._classify_text_with_cutover", return_value=fake_result), \
         patch("src.webhook_handler.send_text_message", return_value=True), \
         patch("src.tools.shadow.run_shadow_classification") as mock_shadow:
        post_webhook(client, whatsapp_text_payload("972500000001", "hi there"))

    mock_shadow.assert_not_called()


def test_parsed_intent_is_saved_using_the_cutover_result(client, make_user):
    """The DB should show the new tool name (e.g. 'get_weather'), not an
    old-style intent name, when cutover actually handles a message - a
    visible signal in message history that cutover took effect for real."""
    user_id = make_user(whatsapp_number="972500000001")
    fake_result = {"intent": "get_weather", "reply": "18 degrees and sunny"}

    with patch("src.webhook_handler._classify_text_with_cutover", return_value=fake_result), \
         patch("src.webhook_handler.send_text_message", return_value=True):
        post_webhook(client, whatsapp_text_payload("972500000001", "מה מזג האוויר"))

    # Filtered to the incoming row specifically (whatsapp_message_id is only
    # ever set on the incoming side) - the outgoing reply this same message
    # also produces has no parsed_intent (NULL) by design, and an
    # unqualified "last row" query would grab that one instead.
    conn = models.get_connection()
    try:
        row = conn.execute(
            "SELECT parsed_intent FROM messages WHERE whatsapp_message_id = ?", ("wamid.TEST1",)
        ).fetchone()
    finally:
        conn.close()
    assert row["parsed_intent"] == "get_weather"

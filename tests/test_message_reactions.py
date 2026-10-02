"""
Batch 11 (2026-09-14): content-matched WhatsApp reactions. _pick_reaction_emoji
and _react_to_message_in_background are tested directly and synchronously
(not through the real background thread), the same deliberate choice
test_shadow_classification.py documents for _embed_message_in_background -
threading.Thread is a process-wide singleton, so globally disabling it would
affect unrelated tests in the same session. The one wiring test below scopes
its patch of threading.Thread to a single `with` block instead of patching
it globally, to check the real webhook path starts the thread with the
right target/args without that risk.
"""
from unittest.mock import ANY, MagicMock, patch

from src.webhook_handler import _pick_reaction_emoji, _react_to_message_in_background

from .conftest import post_webhook, whatsapp_text_payload


def test_pick_reaction_emoji_returns_the_emoji_when_one_fits():
    with patch("src.webhook_handler.call_gemini_json", return_value={"emoji": "🎉"}):
        assert _pick_reaction_emoji("קיבלתי את העבודה!") == "🎉"


def test_pick_reaction_emoji_returns_none_when_nothing_fits():
    with patch("src.webhook_handler.call_gemini_json", return_value={"emoji": None}):
        assert _pick_reaction_emoji("מה השעה") is None


def test_pick_reaction_emoji_returns_none_on_call_failure():
    with patch("src.webhook_handler.call_gemini_json", return_value=None):
        assert _pick_reaction_emoji("הודעה כלשהי") is None


def test_react_to_message_in_background_sends_the_picked_emoji():
    with patch("src.webhook_handler._pick_reaction_emoji", return_value="😂"), \
         patch("src.webhook_handler.send_reaction") as mock_send:
        _react_to_message_in_background("972500000001", "wamid.TEST", "זה ממש מצחיק")

    mock_send.assert_called_once_with("972500000001", "wamid.TEST", "😂")


def test_react_to_message_in_background_sends_nothing_when_no_emoji_fits():
    with patch("src.webhook_handler._pick_reaction_emoji", return_value=None), \
         patch("src.webhook_handler.send_reaction") as mock_send:
        _react_to_message_in_background("972500000001", "wamid.TEST", "מה השעה")

    mock_send.assert_not_called()


def test_react_to_message_in_background_survives_a_pick_failure():
    """12.3 - a reaction going wrong must never look like anything broke;
    this runs on a background thread precisely so it can never affect the
    real reply either way, but it still must not raise."""
    with patch("src.webhook_handler._pick_reaction_emoji", side_effect=RuntimeError("boom")), \
         patch("src.webhook_handler.send_reaction") as mock_send:
        _react_to_message_in_background("972500000001", "wamid.TEST", "הודעה")

    mock_send.assert_not_called()


def test_react_to_message_in_background_survives_a_send_failure():
    with patch("src.webhook_handler._pick_reaction_emoji", return_value="👍"), \
         patch("src.webhook_handler.send_reaction", side_effect=RuntimeError("network error")):
        _react_to_message_in_background("972500000001", "wamid.TEST", "תודה רבה")  # must not raise


def test_webhook_starts_a_background_reaction_thread_with_the_right_args(client, make_user):
    make_user(whatsapp_number="972500000001")
    fake_result = {"intent": "chat", "reply": "hi"}

    with patch("src.webhook_handler._classify_text_with_cutover", return_value=fake_result), \
         patch("src.webhook_handler.send_text_message", return_value=True), \
         patch("src.webhook_handler.threading.Thread") as mock_thread_cls:
        mock_thread_cls.return_value = MagicMock()
        post_webhook(client, whatsapp_text_payload("972500000001", "תודה רבה!"))

    mock_thread_cls.assert_called_once_with(
        target=ANY, args=("972500000001", "wamid.TEST1", "תודה רבה!"), daemon=True,
    )
    mock_thread_cls.return_value.start.assert_called_once()

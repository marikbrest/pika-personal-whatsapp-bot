"""
_transcribe_media / _classify_media_with_cutover (batch 8, 2026-09-14) - the
voice/media cutover for the function-calling migration. Mocks
call_gemini_json_with_media/_classify_text_with_cutover directly rather than
hitting the real (billed) Gemini API - their own correctness is proven
elsewhere (test_gemini_adapter.py, test_tool_cutover.py).
"""
from unittest.mock import patch

from src.webhook_handler import _classify_media_with_cutover, _transcribe_media


def _user():
    return {"id": 1, "timezone": "Asia/Jerusalem", "is_admin": False}


def test_transcribe_media_voice_returns_the_transcript_text():
    with patch(
        "src.webhook_handler.call_gemini_json_with_media",
        return_value={"transcript": "תזכיר לי לקנות חלב"},
    ) as mock_call:
        result = _transcribe_media(b"fake-audio-bytes", "audio/ogg", "voice")

    assert result == "תזכיר לי לקנות חלב"
    prompt_sent = mock_call.call_args.args[0]
    assert "תמלל" in prompt_sent


def test_transcribe_media_image_includes_caption_in_the_prompt():
    with patch(
        "src.webhook_handler.call_gemini_json_with_media",
        return_value={"transcript": "קבלה על 50 שקל"},
    ) as mock_call:
        result = _transcribe_media(b"fake-image-bytes", "image/jpeg", "media", caption="תזכיר לי לשלם")

    assert result == "קבלה על 50 שקל"
    prompt_sent = mock_call.call_args.args[0]
    assert "תזכיר לי לשלם" in prompt_sent


def test_transcribe_media_returns_none_when_gemini_call_fails():
    with patch("src.webhook_handler.call_gemini_json_with_media", return_value=None):
        assert _transcribe_media(b"x", "audio/ogg", "voice") is None


def test_transcribe_media_returns_none_when_transcript_key_missing():
    with patch("src.webhook_handler.call_gemini_json_with_media", return_value={}):
        assert _transcribe_media(b"x", "audio/ogg", "voice") is None


def test_classify_media_with_cutover_runs_transcription_then_text_cutover():
    with patch("src.webhook_handler._transcribe_media", return_value="מה מזג האוויר") as mock_transcribe, \
         patch(
             "src.webhook_handler._classify_text_with_cutover",
             return_value={"intent": "get_weather", "reply": "18 מעלות"},
         ) as mock_classify:
        result = _classify_media_with_cutover(
            b"audio", "audio/ogg", "voice", "", _user(), None, None, None, None,
        )

    mock_transcribe.assert_called_once_with(b"audio", "audio/ogg", "voice", "")
    mock_classify.assert_called_once_with("מה מזג האוויר", _user(), None, None, None, None, None, None)
    assert result == {"intent": "get_weather", "reply": "18 מעלות", "transcript": "מה מזג האוויר"}


def test_classify_media_with_cutover_passes_pending_suggestion_through():
    """Regression test for a real bug (found 2026-09-14 by a same-day
    bug-hunt review, not by any prior unit test - they all mocked
    _classify_text_with_cutover directly, so a missing argument to it never
    surfaced): pending_suggestion used to be silently dropped here while
    pending_draft was already threaded through correctly, meaning a user who
    replied to a pending suggestion with a voice note or image caption
    (instead of typing) could never confirm or dismiss it - confirm_suggestion
    was never offered for that reply, and the suggestion just sat until it
    expired."""
    pending_suggestion = {"id": 1, "confirmation_text": "רוצה שאוסיף אירוע?"}
    with patch("src.webhook_handler._transcribe_media", return_value="כן"), \
         patch(
             "src.webhook_handler._classify_text_with_cutover",
             return_value={"intent": "confirm_suggestion", "reply": "בוצע"},
         ) as mock_classify:
        _classify_media_with_cutover(
            b"audio", "audio/ogg", "voice", "", _user(), None, None, None, None, pending_suggestion,
        )

    mock_classify.assert_called_once_with("כן", _user(), None, None, None, None, pending_suggestion, None)


def test_classify_media_with_cutover_returns_none_when_transcription_fails():
    """Callers must fall back to the old single-call path entirely when
    there is nothing to classify - the text cutover must not even be tried."""
    with patch("src.webhook_handler._transcribe_media", return_value=None), \
         patch("src.webhook_handler._classify_text_with_cutover") as mock_classify:
        result = _classify_media_with_cutover(
            b"audio", "audio/ogg", "voice", "", _user(), None, None, None, None,
        )

    mock_classify.assert_not_called()
    assert result is None


def test_classify_media_with_cutover_passes_caption_through_for_media_kind():
    with patch("src.webhook_handler._transcribe_media", return_value="קבלה") as mock_transcribe, \
         patch("src.webhook_handler._classify_text_with_cutover", return_value={"intent": "chat", "reply": "ok"}):
        _classify_media_with_cutover(
            b"img", "image/jpeg", "media", "מה זה?", _user(), None, None, None, None,
        )

    mock_transcribe.assert_called_once_with(b"img", "image/jpeg", "media", "מה זה?")

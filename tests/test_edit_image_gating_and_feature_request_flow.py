"""
Two 2026-09-26 additions, both reusing already-established patterns rather
than new mechanisms:

1. edit_image's "physically excluded unless a recent photo upload exists"
   gating in _classify_text_with_cutover - same test shape as
   test_tool_cutover.py's respond_to_email_draft coverage, just keyed on
   pending_image_upload instead of pending_draft.

2. send_feature_request_to_developer reached via confirm_suggestion's
   already-existing generic dispatch (no new gated tool/table was built for
   this - see src/tools/batch15.py's own module docstring) - same shape as
   test_forwarded_suggestions.py's confirm/dismiss coverage.
"""
import json
from unittest.mock import MagicMock, patch

from src.db.models import get_pending_suggestion, save_pending_suggestion
from src.webhook_handler import _classify_text_with_cutover, _handle_confirm_suggestion_tool


def _user(user_id=1):
    return {"id": user_id, "timezone": "Asia/Jerusalem", "is_admin": False, "display_name": "יוסי"}


# ===== edit_image gating =====

def test_edit_image_is_excluded_from_the_candidate_tools_when_no_photo_is_pending():
    weather_tool = MagicMock()
    weather_tool.name = "get_weather"
    edit_tool = MagicMock()
    edit_tool.name = "edit_image"

    with patch("src.tools.registry.tools_for", return_value=[weather_tool, edit_tool]), \
         patch("src.tools.gemini_adapter.classify_with_tools", return_value=("get_weather", {})) as mock_classify, \
         patch("src.tools.dispatch.execute_tool", return_value="18 degrees"):
        _classify_text_with_cutover("מה מזג האוויר", _user(), None, None, None, None)

    offered_tools = mock_classify.call_args.args[1]
    assert edit_tool not in offered_tools
    assert weather_tool in offered_tools


def test_edit_image_is_offered_and_context_is_prepended_when_a_photo_is_pending():
    edit_tool = MagicMock()
    edit_tool.name = "edit_image"
    pending_image_upload = {"media_id": "media-id-1", "mime_type": "image/jpeg"}

    with patch("src.tools.registry.tools_for", return_value=[edit_tool]), \
         patch(
             "src.tools.gemini_adapter.classify_with_tools",
             return_value=("edit_image", {"instructions": "black and white"}),
         ) as mock_classify, \
         patch("src.tools.dispatch.execute_tool", return_value="🎨 הנה התמונה הערוכה!"):
        _classify_text_with_cutover(
            "תהפוך אותה לשחור לבן", _user(), None, None, None, None, None, pending_image_upload,
        )

    offered_tools = mock_classify.call_args.args[1]
    assert edit_tool in offered_tools
    contents_sent = mock_classify.call_args.args[0]
    assert "edit_image" in contents_sent


# ===== send_feature_request_to_developer via confirm_suggestion =====

def test_confirming_a_feature_request_suggestion_dispatches_send_feature_request(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    save_pending_suggestion(
        1, "send_feature_request_to_developer",
        json.dumps({"request_text": "voice replies"}),
        "אני לא יודע לעשות את זה כרגע, לשלוח למפתח בקשה?", "אתה יכול לענות לי בהודעות קוליות?",
    )

    with patch("src.webhook_handler.send_text_message") as mock_send:
        reply = _handle_confirm_suggestion_tool(_user(), {"action": "confirm"})

    mock_send.assert_called_once()
    assert "voice replies" in mock_send.call_args.kwargs["body"]
    assert "שלחתי" in reply
    # the suggestion is consumed - a second confirm has nothing left to act on
    assert get_pending_suggestion(1) is None


def test_dismissing_a_feature_request_suggestion_sends_nothing(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    save_pending_suggestion(
        1, "send_feature_request_to_developer",
        json.dumps({"request_text": "voice replies"}),
        "לשלוח למפתח בקשה?", "מקור",
    )

    with patch("src.webhook_handler.send_text_message") as mock_send:
        reply = _handle_confirm_suggestion_tool(_user(), {"action": "dismiss"})

    mock_send.assert_not_called()
    assert reply

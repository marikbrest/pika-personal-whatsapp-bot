"""
_handle_generate_image / _handle_edit_image / _handle_send_feature_request
(2026-09-26) - image generation/editing, and the "notify the developer about
an unsupported request" escape hatch. Mocks the Gemini/WhatsApp integration
calls directly (patched at src.webhook_handler, where they were imported -
same convention as e.g. test_calendar_attendee_family_members.py patching
src.webhook_handler.create_event), and uses real DB fixtures for anything
that reads/writes actual rows (pending_image_uploads, users).
"""
from unittest.mock import patch

from src.db.models import get_pending_image_upload, save_pending_image_upload
from src.webhook_handler import _handle_edit_image, _handle_generate_image, _handle_send_feature_request


def _user(user_id=1, whatsapp_number="972500000001", display_name="יוסי"):
    return {"id": user_id, "whatsapp_number": whatsapp_number, "display_name": display_name, "timezone": "Asia/Jerusalem"}


# ===== _handle_generate_image =====

def test_generate_image_sends_the_image_and_confirms(db_path, make_user):
    with patch("src.webhook_handler.generate_image", return_value=(b"bytes", "image/png")) as mock_gen, \
         patch("src.webhook_handler.send_image_bytes", return_value=True) as mock_send:
        reply = _handle_generate_image(_user(), {"prompt": "a red circle"})

    mock_gen.assert_called_once_with("a red circle")
    mock_send.assert_called_once_with("972500000001", b"bytes", "image/png")
    assert "תמונה" in reply


def test_generate_image_with_no_prompt_asks_what_to_draw():
    with patch("src.webhook_handler.generate_image") as mock_gen:
        reply = _handle_generate_image(_user(), {})

    mock_gen.assert_not_called()
    assert reply


def test_generate_image_reports_failure_when_generation_fails():
    with patch("src.webhook_handler.generate_image", return_value=None), \
         patch("src.webhook_handler.send_image_bytes") as mock_send:
        reply = _handle_generate_image(_user(), {"prompt": "a red circle"})

    mock_send.assert_not_called()
    assert "לא הצלחתי" in reply


def test_generate_image_reports_failure_when_sending_fails():
    with patch("src.webhook_handler.generate_image", return_value=(b"bytes", "image/png")), \
         patch("src.webhook_handler.send_image_bytes", return_value=False):
        reply = _handle_generate_image(_user(), {"prompt": "a red circle"})

    assert "השליחה נכשלה" in reply


# ===== _handle_edit_image =====

def test_edit_image_downloads_edits_and_sends(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי")
    save_pending_image_upload(1, "media-id-1", "image/jpeg")

    with patch("src.webhook_handler.download_media", return_value=(b"original", "image/jpeg")) as mock_dl, \
         patch("src.webhook_handler.edit_image", return_value=(b"edited", "image/png")) as mock_edit, \
         patch("src.webhook_handler.send_image_bytes", return_value=True) as mock_send:
        reply = _handle_edit_image(_user(), {"instructions": "make it black and white"})

    mock_dl.assert_called_once_with("media-id-1")
    mock_edit.assert_called_once_with(b"original", "image/jpeg", "make it black and white")
    mock_send.assert_called_once_with("972500000001", b"edited", "image/png")
    assert "ערוכה" in reply
    # the pending upload is cleared after a successful edit - no chaining (see the handler's own docstring)
    assert get_pending_image_upload(1) is None


def test_edit_image_with_no_pending_upload_asks_to_send_a_photo(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי")
    with patch("src.webhook_handler.download_media") as mock_dl:
        reply = _handle_edit_image(_user(), {"instructions": "make it black and white"})

    mock_dl.assert_not_called()
    assert "תמונה" in reply


def test_edit_image_reports_failure_when_the_original_download_fails(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי")
    save_pending_image_upload(1, "media-id-1", "image/jpeg")

    with patch("src.webhook_handler.download_media", return_value=None), \
         patch("src.webhook_handler.edit_image") as mock_edit:
        reply = _handle_edit_image(_user(), {"instructions": "make it black and white"})

    mock_edit.assert_not_called()
    assert get_pending_image_upload(1) is None  # cleared even on failure - not left stale
    assert "לא הצלחתי" in reply


def test_edit_image_reports_failure_when_the_edit_call_fails(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי")
    save_pending_image_upload(1, "media-id-1", "image/jpeg")

    with patch("src.webhook_handler.download_media", return_value=(b"original", "image/jpeg")), \
         patch("src.webhook_handler.edit_image", return_value=None), \
         patch("src.webhook_handler.send_image_bytes") as mock_send:
        reply = _handle_edit_image(_user(), {"instructions": "make it black and white"})

    mock_send.assert_not_called()
    assert "לא הצלחתי" in reply


# ===== _handle_send_feature_request =====

def test_send_feature_request_notifies_every_admin_only(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    make_user(whatsapp_number="972500000002", display_name="רונית", is_admin=False)
    make_user(whatsapp_number="972500000003", display_name="Admin2", is_admin=True)

    with patch("src.webhook_handler.send_text_message") as mock_send:
        reply = _handle_send_feature_request(_user(2, "972500000002", "רונית"), {"request_text": "voice replies"})

    assert mock_send.call_count == 2
    recipients = {c.kwargs["to"] for c in mock_send.call_args_list}
    assert recipients == {"972500000001", "972500000003"}
    for c in mock_send.call_args_list:
        assert "voice replies" in c.kwargs["body"]
        assert "רונית" in c.kwargs["body"]
    assert "שלחתי" in reply


def test_send_feature_request_with_no_text_asks_again():
    with patch("src.webhook_handler.send_text_message") as mock_send:
        reply = _handle_send_feature_request(_user(), {})

    mock_send.assert_not_called()
    assert reply


def test_send_feature_request_isolates_a_single_admin_send_failure(db_path, make_user):
    """Per-admin error isolation (12.3) - one admin's broken connection must
    never hide the request from the others."""
    make_user(whatsapp_number="972500000001", display_name="Admin1", is_admin=True)
    make_user(whatsapp_number="972500000002", display_name="Admin2", is_admin=True)

    with patch("src.webhook_handler.send_text_message", side_effect=[RuntimeError("boom"), True]) as mock_send:
        reply = _handle_send_feature_request(_user(), {"request_text": "voice replies"})

    assert mock_send.call_count == 2
    assert "שלחתי" in reply

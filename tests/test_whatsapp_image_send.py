"""
upload_media/send_image_bytes (2026-09-26) - sending a generated/edited
image, which only ever exists as raw bytes in memory (never a hosted URL).
Mocks the shared httpx client directly, same pattern as
test_whatsapp_reaction.py/test_whatsapp_templates.py.
"""
from unittest.mock import MagicMock, patch

from src.integrations import whatsapp


def _response(status_code, json_body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = str(json_body or {})
    resp.json.return_value = json_body or {}
    return resp


def test_upload_media_returns_the_media_id_on_success():
    with patch.object(whatsapp, "_client") as mock_client:
        mock_client.post.return_value = _response(200, {"id": "media-id-123"})
        result = whatsapp.upload_media(b"fake-bytes", "image/png")

    assert result == "media-id-123"
    kwargs = mock_client.post.call_args.kwargs
    assert kwargs["data"]["messaging_product"] == "whatsapp"
    assert kwargs["files"]["file"][1] == b"fake-bytes"
    assert kwargs["files"]["file"][2] == "image/png"


def test_upload_media_returns_none_on_failure():
    with patch.object(whatsapp, "_client") as mock_client:
        mock_client.post.return_value = _response(400, {"error": {"message": "bad file"}})
        assert whatsapp.upload_media(b"fake-bytes", "image/png") is None


def test_send_image_bytes_uploads_then_sends_an_image_message():
    with patch.object(whatsapp, "upload_media", return_value="media-id-123") as mock_upload, \
         patch.object(whatsapp, "_client") as mock_client:
        mock_client.post.return_value = _response(200, {"messages": [{"id": "wamid.ABC"}]})
        result = whatsapp.send_image_bytes("972500000001", b"fake-bytes", "image/png")

    assert result is True
    mock_upload.assert_called_once_with(b"fake-bytes", "image/png")
    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["type"] == "image"
    assert payload["to"] == "972500000001"
    assert payload["image"] == {"id": "media-id-123"}


def test_send_image_bytes_returns_false_when_upload_fails():
    with patch.object(whatsapp, "upload_media", return_value=None) as mock_upload, \
         patch.object(whatsapp, "_client") as mock_client:
        result = whatsapp.send_image_bytes("972500000001", b"fake-bytes", "image/png")

    assert result is False
    mock_upload.assert_called_once()
    mock_client.post.assert_not_called()

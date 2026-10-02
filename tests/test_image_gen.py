"""
src/integrations/image_gen.py - generate_image/edit_image (2026-09-26).
Mocks the Gemini client directly (via _get_client), same pattern the rest of
this codebase's Gemini integration tests use - never hits the real (billed)
API.
"""
from unittest.mock import MagicMock, patch

from src.integrations.image_gen import edit_image, generate_image


def _response_with_image(image_bytes=b"fake-png-bytes", mime_type="image/png", text=None):
    part_image = MagicMock()
    part_image.inline_data.data = image_bytes
    part_image.inline_data.mime_type = mime_type
    part_image.text = None

    parts = [part_image]
    if text is not None:
        part_text = MagicMock()
        part_text.inline_data = None
        part_text.text = text
        parts.append(part_text)

    response = MagicMock()
    response.candidates[0].content.parts = parts
    response.usage_metadata = None
    return response


def test_generate_image_returns_bytes_and_mime_type():
    client = MagicMock()
    client.models.generate_content.return_value = _response_with_image()
    with patch("src.integrations.image_gen._get_client", return_value=client):
        result = generate_image("a red circle")

    assert result == (b"fake-png-bytes", "image/png")
    kwargs = client.models.generate_content.call_args.kwargs
    assert kwargs["model"] == "gemini-2.5-flash-image"
    assert kwargs["contents"] == "a red circle"


def test_generate_image_returns_none_when_no_image_part_present():
    response = MagicMock()
    response.candidates[0].content.parts = []
    response.usage_metadata = None
    client = MagicMock()
    client.models.generate_content.return_value = response
    with patch("src.integrations.image_gen._get_client", return_value=client):
        assert generate_image("a red circle") is None


def test_generate_image_returns_none_when_the_api_call_raises():
    client = MagicMock()
    client.models.generate_content.side_effect = RuntimeError("boom")
    with patch("src.integrations.image_gen._get_client", return_value=client):
        assert generate_image("a red circle") is None


def test_edit_image_sends_the_original_image_and_instructions():
    client = MagicMock()
    client.models.generate_content.return_value = _response_with_image(b"edited-bytes")
    with patch("src.integrations.image_gen._get_client", return_value=client):
        result = edit_image(b"original-bytes", "image/png", "make it black and white")

    assert result == (b"edited-bytes", "image/png")
    kwargs = client.models.generate_content.call_args.kwargs
    assert kwargs["contents"][0] == "make it black and white"


def test_edit_image_returns_none_when_the_api_call_raises():
    client = MagicMock()
    client.models.generate_content.side_effect = RuntimeError("boom")
    with patch("src.integrations.image_gen._get_client", return_value=client):
        assert edit_image(b"x", "image/png", "change it") is None

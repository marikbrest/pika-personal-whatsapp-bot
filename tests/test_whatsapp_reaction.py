"""
send_reaction (2026-09-14) - WhatsApp emoji reactions on a message the bot
received, identified by its own wamid. Mocks the shared httpx client
directly, same as test_whatsapp_templates.py.
"""
from unittest.mock import MagicMock, patch

from src.integrations import whatsapp


def _response(status_code, json_body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = str(json_body or {})
    resp.json.return_value = json_body or {}
    return resp


def test_send_reaction_builds_correct_payload():
    with patch.object(whatsapp, "_client") as mock_client:
        mock_client.post.return_value = _response(200)
        result = whatsapp.send_reaction(to="972500000001", message_id="wamid.ABC123", emoji="👍")

    assert result is True
    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["type"] == "reaction"
    assert payload["to"] == "972500000001"
    assert payload["reaction"] == {"message_id": "wamid.ABC123", "emoji": "👍"}


def test_send_reaction_can_remove_a_reaction_with_an_empty_emoji():
    with patch.object(whatsapp, "_client") as mock_client:
        mock_client.post.return_value = _response(200)
        whatsapp.send_reaction(to="972500000001", message_id="wamid.ABC123", emoji="")

    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["reaction"]["emoji"] == ""


def test_send_reaction_returns_false_on_failure():
    with patch.object(whatsapp, "_client") as mock_client:
        mock_client.post.return_value = _response(400, {"error": {"code": 100, "message": "bad request"}})
        result = whatsapp.send_reaction(to="972500000001", message_id="wamid.ABC123", emoji="👍")

    assert result is False

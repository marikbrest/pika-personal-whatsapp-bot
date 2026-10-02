"""
Regression guard (2026-10-02): a few tool names (add_contact, web_search,
connect_google) are identical to old-classifier intent names. When the tools
pipeline had already executed one of them, the legacy action dispatch in
_process_message ran it a SECOND time on the tool envelope - add_contact
crashed with KeyError 'contact' (user got the generic error reply),
web_search ran the search twice, and connect_google appended a second OAuth
link. Tool envelopes now carry tool_executed=True and skip that dispatch.
"""
from unittest.mock import patch

from src.db import models

from .conftest import post_webhook, whatsapp_text_payload


def _send(client, text, tool_call):
    with patch("src.tools.gemini_adapter.classify_with_tools", return_value=tool_call), \
         patch("src.webhook_handler.send_text_message", return_value=True) as mock_send:
        post_webhook(client, whatsapp_text_payload("972500000001", text))
    mock_send.assert_called_once()
    return mock_send.call_args.kwargs["body"]


def test_add_contact_via_tools_saves_once_and_replies_normally(client, make_user):
    user_id = make_user(whatsapp_number="972500000001")
    args = {"name": "Dana", "whatsapp_number": "972501234567", "reply_text": "Saved Dana"}

    reply = _send(client, "add contact Dana 0501234567", ("add_contact", args))

    assert reply == "Saved Dana"
    contacts = models.list_contacts(user_id)
    assert [c["name"] for c in contacts] == ["Dana"]


def test_web_search_via_tools_is_not_dispatched_a_second_time(client, make_user):
    make_user(whatsapp_number="972500000001")
    with patch("src.tools.batch2._handle_web_search", return_value="result A") as tool_handler, \
         patch("src.webhook_handler._handle_web_search", return_value="result B") as legacy_handler:
        reply = _send(client, "search the web", ("web_search", {"query": "x"}))

    assert reply == "result A"
    tool_handler.assert_called_once()
    legacy_handler.assert_not_called()


def test_connect_google_via_tools_has_a_single_link(client, make_user):
    make_user(whatsapp_number="972500000001")
    with patch("src.webhook_handler.build_auth_url", return_value="https://example.test/auth") as mock_url:
        reply = _send(client, "connect google", ("connect_google", {"reply_text": "Here you go"}))

    assert reply == "Here you go\n\nhttps://example.test/auth"
    mock_url.assert_called_once()

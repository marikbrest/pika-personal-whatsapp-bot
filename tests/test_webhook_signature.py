"""
PRD 14.1/14.2 - the webhook is the one route reachable from the open
internet with no host restriction. These tests cover the two gates that
stand between "anyone on the internet" and "code that calls Gemini/WhatsApp
on the bot's dime": the HMAC signature, and the phone-number allowlist.
"""
import json
from unittest.mock import patch

from .conftest import WHATSAPP_APP_SECRET, post_webhook, sign_payload, whatsapp_text_payload


def test_verify_get_returns_challenge_for_correct_token(client):
    resp = client.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "test-webhook-verify-token", "hub.challenge": "12345"},
    )
    assert resp.status_code == 200
    assert resp.text == "12345"


def test_verify_get_rejects_wrong_token(client):
    resp = client.get(
        "/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong-token", "hub.challenge": "12345"},
    )
    assert resp.status_code == 403


def test_post_rejects_missing_signature_header(client):
    payload = whatsapp_text_payload("972500000001", "hi")
    resp = client.post("/webhook", json=payload)
    assert resp.status_code == 403


def test_post_rejects_signature_computed_with_wrong_secret(client):
    payload = whatsapp_text_payload("972500000001", "hi")
    raw_body = json.dumps(payload).encode("utf-8")
    resp = client.post(
        "/webhook",
        content=raw_body,
        headers={"X-Hub-Signature-256": sign_payload(raw_body, secret="not-the-real-secret")},
    )
    assert resp.status_code == 403


def test_post_rejects_signature_valid_for_a_different_body(client):
    """A signature that is well-formed and would be valid for SOME body, just
    not this one - catches an implementation that checks 'is this a
    plausible signature' instead of 'does this signature match this exact
    body'."""
    payload = whatsapp_text_payload("972500000001", "hi")
    real_body = json.dumps(payload).encode("utf-8")
    tampered_body = json.dumps(whatsapp_text_payload("972500000001", "TAMPERED")).encode("utf-8")
    resp = client.post(
        "/webhook",
        content=tampered_body,
        headers={"X-Hub-Signature-256": sign_payload(real_body)},
    )
    assert resp.status_code == 403


def test_post_accepts_valid_signature_on_a_status_webhook(client):
    """A delivery-status callback (no 'messages' key) is a legitimate,
    frequent webhook shape from Meta and must be accepted (200) without
    attempting to process anything as a message."""
    status_payload = {
        "entry": [{"id": "0", "changes": [{"value": {"statuses": [{"id": "wamid.X", "status": "delivered"}]}, "field": "messages"}]}]
    }
    resp = post_webhook(client, status_payload)
    assert resp.status_code == 200


def test_status_webhook_is_handed_to_handle_delivery_status(client):
    """2026-09-18 bug fix wiring: every status callback must actually reach
    handle_delivery_status (the async 24h-window-fallback retry mechanism)
    - not just be logged and dropped, which is what this webhook did before
    a real 'failed' status for an already-accepted reminder send went
    unnoticed and unrecovered."""
    status_payload = {
        "entry": [{"id": "0", "changes": [{"value": {"statuses": [
            {"id": "wamid.X", "status": "failed", "recipient_id": "972500000001", "errors": [{"code": 131047}]}
        ]}, "field": "messages"}]}]
    }
    with patch("src.webhook_handler.handle_delivery_status") as mock_handle:
        resp = post_webhook(client, status_payload)

    assert resp.status_code == 200
    mock_handle.assert_called_once_with("wamid.X", "failed", [{"code": 131047}])


def test_post_ignores_malformed_payload_without_crashing(client):
    """PRD 12.3 - an unexpected payload shape must not 500; Meta (or anyone
    who guesses the URL, since this route has no per-request auth beyond the
    signature which they'd also need) gets a clean 200."""
    raw_body = json.dumps({"entry": [{"changes": [{"value": "not-a-dict"}]}]}).encode("utf-8")
    resp = client.post(
        "/webhook", content=raw_body, headers={"X-Hub-Signature-256": sign_payload(raw_body)}
    )
    assert resp.status_code == 200


def test_unknown_number_is_silently_ignored(client, make_user):
    """14.2 - a signature-valid message from a number NOT in the allowlist
    must not be processed: no Gemini call (money), no WhatsApp reply sent,
    no DB row. make_user is called for a DIFFERENT number, to prove presence
    of some users doesn't make the endpoint permissive in general."""
    make_user(whatsapp_number="972500000001")

    with patch("src.webhook_handler.parse_message") as mock_parse, \
         patch("src.webhook_handler.send_text_message") as mock_send:
        resp = post_webhook(client, whatsapp_text_payload("972599999999", "hello"))

    assert resp.status_code == 200
    mock_parse.assert_not_called()
    mock_send.assert_not_called()


def test_duplicate_message_id_is_processed_only_once(client, make_user):
    """12.1 - Meta may redeliver the same webhook; whatsapp_message_id is
    UNIQUE in the schema and message_exists() is checked before any
    processing. Send the identical payload twice and confirm the expensive
    path (parse_message / Gemini) only ran once."""
    make_user(whatsapp_number="972500000001")
    payload = whatsapp_text_payload("972500000001", "hi", message_id="wamid.DUPLICATE")

    fake_result = {"intent": "chat", "reply": "שלום"}
    with patch("src.webhook_handler.parse_message", return_value=fake_result) as mock_parse, \
         patch("src.webhook_handler.send_text_message", return_value=True):
        post_webhook(client, payload)
        post_webhook(client, payload)

    assert mock_parse.call_count == 1

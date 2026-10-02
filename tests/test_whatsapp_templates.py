"""
send_template_message / send_text_or_template (2026-09-13) - the 24h-window
fallback that lets a proactive WhatsApp message (reminder, package update,
Google reconnect alert) actually reach a recipient who hasn't messaged the
bot recently. WhatsApp forbids free-form text in that case (error 131047);
a pre-approved template is the only message type still allowed.

Mocks the shared httpx client directly, the same one send_text_message has
always used - these are fast unit tests against payload-building and
fallback logic, not integration tests against the real Graph API.
"""
from unittest.mock import MagicMock, patch

from src.integrations import whatsapp

_OUTSIDE_WINDOW_ERROR = {"error": {"code": 131047, "message": "Re-engagement message"}}
_UNRELATED_ERROR = {"error": {"code": 131026, "message": "Message undeliverable"}}
_TEMPLATE_NOT_APPROVED_ERROR = {"error": {"code": 132001, "message": "Template not found"}}


def _response(status_code, json_body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = str(json_body or {})
    resp.json.return_value = json_body or {}
    return resp


def test_send_template_message_builds_correct_payload():
    with patch.object(whatsapp, "_client") as mock_client:
        mock_client.post.return_value = _response(200)
        result = whatsapp.send_template_message(
            to="972500000001",
            template_name="reminder_notification",
            language_code="he",
            body_params=["לקחת תרופה"],
        )

    assert result is True
    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["type"] == "template"
    assert payload["template"]["name"] == "reminder_notification"
    assert payload["template"]["language"] == {"code": "he"}
    assert payload["template"]["components"] == [
        {"type": "body", "parameters": [{"type": "text", "text": "לקחת תרופה"}]}
    ]


def test_send_template_message_with_no_params_omits_body_component():
    with patch.object(whatsapp, "_client") as mock_client:
        mock_client.post.return_value = _response(200)
        whatsapp.send_template_message(to="972500000001", template_name="hello_world", language_code="en_US")

    payload = mock_client.post.call_args.kwargs["json"]
    assert payload["template"]["components"] == []


def test_send_text_or_template_uses_text_when_it_succeeds():
    with patch.object(whatsapp, "_client") as mock_client:
        mock_client.post.return_value = _response(200)
        result = whatsapp.send_text_or_template(
            to="972500000001", body="שלום", template_name="reminder_notification",
            language_code="he", body_params=["x"],
        )

    assert result is True
    assert mock_client.post.call_count == 1
    assert mock_client.post.call_args.kwargs["json"]["type"] == "text"


def test_send_text_or_template_falls_back_on_24h_window_error():
    with patch.object(whatsapp, "_client") as mock_client:
        mock_client.post.side_effect = [_response(400, _OUTSIDE_WINDOW_ERROR), _response(200)]
        result = whatsapp.send_text_or_template(
            to="972500000001", body="שלום", template_name="reminder_notification",
            language_code="he", body_params=["לקחת תרופה"],
        )

    assert result is True
    assert mock_client.post.call_count == 2
    first_payload = mock_client.post.call_args_list[0].kwargs["json"]
    second_payload = mock_client.post.call_args_list[1].kwargs["json"]
    assert first_payload["type"] == "text"
    assert second_payload["type"] == "template"
    assert second_payload["template"]["name"] == "reminder_notification"


def test_send_text_or_template_does_not_fall_back_on_unrelated_error():
    """A permanent failure unrelated to the 24h window (e.g. an invalid
    number) must not trigger a template send - that would just fail the same
    way for a different reason."""
    with patch.object(whatsapp, "_client") as mock_client:
        mock_client.post.return_value = _response(400, _UNRELATED_ERROR)
        result = whatsapp.send_text_or_template(
            to="972500000001", body="שלום", template_name="reminder_notification",
            language_code="he", body_params=["x"],
        )

    assert result is False
    assert mock_client.post.call_count == 1  # never attempted the template


def test_send_text_or_template_returns_false_when_the_template_fallback_also_fails():
    """Before Meta approves a template, the fallback itself fails too - this
    must behave exactly like send_text_message always has (return False),
    not raise. Safe to wire in ahead of approval for exactly this reason."""
    with patch.object(whatsapp, "_client") as mock_client:
        mock_client.post.side_effect = [
            _response(400, _OUTSIDE_WINDOW_ERROR),
            _response(400, _TEMPLATE_NOT_APPROVED_ERROR),
        ]
        result = whatsapp.send_text_or_template(
            to="972500000001", body="שלום", template_name="reminder_notification",
            language_code="he", body_params=["x"],
        )

    assert result is False
    assert mock_client.post.call_count == 2


class TestAsyncTwentyFourHourWindowFallback:
    """2026-09-18 bug fix: found live that WhatsApp does not always reject an
    outside-24h-window free-text send synchronously with error 131047 in
    send_text_or_template's own response - it can return 200 OK there and
    only report the same rejection later, via an async delivery-status
    webhook (3 real reminder sends to contacts outside the window all did
    exactly this - accepted at send time, never delivered). handle_delivery_
    status is the async counterpart of the synchronous check, consulting
    _pending_24h_window_fallback (populated by every "successful"
    send_text_or_template call) to retry via template when this happens."""

    def setup_method(self):
        whatsapp._pending_24h_window_fallback.clear()

    def teardown_method(self):
        whatsapp._pending_24h_window_fallback.clear()

    def test_a_successful_send_registers_its_message_id_for_fallback_tracking(self):
        with patch.object(whatsapp, "_client") as mock_client:
            mock_client.post.return_value = _response(200, {"messages": [{"id": "wamid.ABC"}]})
            whatsapp.send_text_or_template(
                to="972500000001", body="שלום", template_name="reminder_notification",
                language_code="he", body_params=["x"],
            )

        assert "wamid.ABC" in whatsapp._pending_24h_window_fallback
        assert whatsapp._pending_24h_window_fallback["wamid.ABC"]["to"] == "972500000001"

    def test_a_late_24h_window_failure_retries_via_template(self):
        with patch.object(whatsapp, "_client") as mock_client:
            mock_client.post.return_value = _response(200, {"messages": [{"id": "wamid.ABC"}]})
            whatsapp.send_text_or_template(
                to="972500000001", body="שלום", template_name="reminder_notification",
                language_code="he", body_params=["לקחת תרופה"],
            )

        with patch.object(whatsapp, "send_template_message") as mock_template_send:
            whatsapp.handle_delivery_status("wamid.ABC", "failed", [{"code": 131047}])

        mock_template_send.assert_called_once_with("972500000001", "reminder_notification", "he", ["לקחת תרופה"])
        assert "wamid.ABC" not in whatsapp._pending_24h_window_fallback  # consumed, not retried twice

    def test_a_late_failure_with_an_unrelated_error_code_does_not_retry(self):
        with patch.object(whatsapp, "_client") as mock_client:
            mock_client.post.return_value = _response(200, {"messages": [{"id": "wamid.ABC"}]})
            whatsapp.send_text_or_template(
                to="972500000001", body="שלום", template_name="reminder_notification",
                language_code="he", body_params=["x"],
            )

        with patch.object(whatsapp, "send_template_message") as mock_template_send:
            whatsapp.handle_delivery_status("wamid.ABC", "failed", [{"code": 131026}])

        mock_template_send.assert_not_called()

    def test_a_normal_delivered_status_clears_tracking_without_retrying(self):
        with patch.object(whatsapp, "_client") as mock_client:
            mock_client.post.return_value = _response(200, {"messages": [{"id": "wamid.ABC"}]})
            whatsapp.send_text_or_template(
                to="972500000001", body="שלום", template_name="reminder_notification",
                language_code="he", body_params=["x"],
            )

        with patch.object(whatsapp, "send_template_message") as mock_template_send:
            whatsapp.handle_delivery_status("wamid.ABC", "delivered")

        mock_template_send.assert_not_called()
        assert "wamid.ABC" not in whatsapp._pending_24h_window_fallback

    def test_an_unregistered_message_id_is_a_harmless_no_op(self):
        with patch.object(whatsapp, "send_template_message") as mock_template_send:
            whatsapp.handle_delivery_status("wamid.NEVER_SEEN", "failed", [{"code": 131047}])

        mock_template_send.assert_not_called()

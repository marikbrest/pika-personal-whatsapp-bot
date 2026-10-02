"""
Regression tests for the header-injection fix in gmail.py (2026-09-13):
to_address/subject flow from a Gemini JSON response driven by the user's own
WhatsApp text, and were passed into email.mime.text.MIMEText unsanitized. A
literal newline in either could smuggle in an extra MIME header (e.g. a
forged Bcc:). The approval step the bot always shows before send() makes
this hard to exploit blind, but that is a human catching it, not a
guarantee - these tests are about the code-level guarantee.
"""
import base64
from email import message_from_bytes
from unittest.mock import MagicMock, patch

from src.integrations.gmail import _strip_header_injection, send_email


def test_strip_header_injection_removes_crlf():
    assert _strip_header_injection("normal subject") == "normal subject"
    assert _strip_header_injection("evil\r\nBcc: attacker@evil.com") == "evilBcc: attacker@evil.com"
    assert _strip_header_injection("evil\nX-Injected: yes") == "evilX-Injected: yes"
    assert "\n" not in _strip_header_injection("a\r\nb\nc\rd")
    assert "\r" not in _strip_header_injection("a\r\nb\nc\rd")


def test_send_email_strips_injection_attempt_from_subject_and_to(monkeypatch):
    """End-to-end: even a caller that skipped _strip_header_injection itself
    (webhook_handler passes whatever Gemini gave it) cannot get a second
    header into the message send() actually builds."""
    captured = {}

    def capture_send(userId, body):
        captured["body"] = body
        result = MagicMock()
        result.execute.return_value = {"id": "msg1"}
        return result

    fake_service = MagicMock()
    fake_service.users.return_value.messages.return_value.send.side_effect = capture_send

    with patch("src.integrations.gmail._get_gmail_service", return_value=fake_service):
        send_email(
            user_id=1,
            to_address="real@example.com\r\nBcc: attacker@evil.com",
            subject="innocent subject\nX-Injected-Header: yes",
            body="hello",
        )

    raw_bytes = base64.urlsafe_b64decode(captured["body"]["raw"])
    parsed = message_from_bytes(raw_bytes)

    assert parsed.get_all("Bcc") is None
    assert parsed.get_all("X-Injected-Header") is None
    assert "\n" not in parsed["To"]
    assert "\n" not in parsed["Subject"]
    assert parsed["To"] == "real@example.comBcc: attacker@evil.com"  # neutered, not silently dropped

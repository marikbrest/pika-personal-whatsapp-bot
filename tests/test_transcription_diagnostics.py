"""Failure causes remain observable without leaking responses, exceptions or audio."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from src import transcription as tr
from src.integrations import gemini
from src.transcription_diagnostics import failure

PRIVATE = "private recording, person@example.invalid, key-secret-marker"


@pytest.fixture
def capture(caplog):
    caplog.set_level("INFO", logger="uvicorn.error.transcription")
    return caplog


def assert_safe(capture, stage, reason):
    messages = [r.getMessage() for r in capture.records if r.name == "uvicorn.error.transcription"]
    assert f"transcription_failure stage={stage} reason={reason}" in messages
    assert PRIVATE not in capture.text
    assert "key-secret-marker" not in capture.text


@pytest.mark.parametrize("stage,reason", [(PRIVATE, PRIVATE), ("gemini", PRIVATE), ([], {})])
def test_closed_vocabulary_cannot_log_untrusted_values(capture, stage, reason):
    failure(stage, reason)
    assert PRIVATE not in capture.text
    assert "reason=unknown" in capture.text


@pytest.mark.parametrize("finish,text,expected", [
    ("MAX_TOKENS", PRIVATE, "output_limit"),
    ("SAFETY", PRIVATE, "safety_block"),
    (PRIVATE, PRIVATE, "finish_not_stop"),
    ("STOP", PRIVATE, "invalid_json"),
    ("STOP", json.dumps([PRIVATE]), "invalid_shape"),
])
def test_gemini_response_rejection_logs_cause_without_response(monkeypatch, capture, finish, text, expected):
    response = SimpleNamespace(candidates=[SimpleNamespace(finish_reason=finish)], text=text, usage_metadata=None)
    generate = Mock(return_value=response)
    monkeypatch.setattr(gemini, "_get_client", lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    assert gemini.call_transcription_json(PRIVATE, PRIVATE.encode(), "audio/ogg") is None
    assert_safe(capture, "gemini", expected)
    generate.assert_called_once()  # diagnostics must not add retries/cost


def test_missing_candidates_is_distinct(monkeypatch, capture):
    response = SimpleNamespace(candidates=[], text=PRIVATE, usage_metadata=None)
    monkeypatch.setattr(gemini, "_get_client", lambda: SimpleNamespace(models=SimpleNamespace(generate_content=Mock(return_value=response))))
    assert gemini.call_transcription_json(PRIVATE, b"audio", "audio/ogg") is None
    assert_safe(capture, "gemini", "no_candidates")


@pytest.mark.parametrize("error,expected", [
    (httpx.ReadTimeout(PRIVATE), "timeout"),
    (httpx.ConnectError(PRIVATE), "connection_error"),
    (RuntimeError(PRIVATE), "provider_exception"),
])
def test_provider_exception_details_never_logged(monkeypatch, capture, error, expected):
    generate = Mock(side_effect=error)
    monkeypatch.setattr(gemini, "_get_client", lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    assert gemini.call_transcription_json(PRIVATE, b"audio", "audio/ogg") is None
    assert_safe(capture, "gemini", expected)
    generate.assert_called_once()


@pytest.mark.parametrize("code,expected", [(429, "rate_limit"), (401, "authentication"), (403, "permission"),
                                           (400, "request_rejected"), (503, "provider_unavailable"),
                                           (PRIVATE, "provider_exception"), (True, "provider_exception")])
def test_only_known_numeric_provider_codes_are_classified(monkeypatch, capture, code, expected):
    error = RuntimeError(PRIVATE)
    error.code = code
    generate = Mock(side_effect=error)
    monkeypatch.setattr(gemini, "_get_client", lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    assert gemini.call_transcription_json(PRIVATE, b"audio", "audio/ogg") is None
    assert_safe(capture, "gemini", expected)


@pytest.mark.parametrize("data,reason", [(None, "no_result"), ([PRIVATE], "invalid_shape"),
                                       ({"other": PRIVATE}, "missing_text"), ({"transcript": " "}, "empty_text"),
                                       ({"transcript": PRIVATE * 1000}, "text_too_long")])
def test_shared_transcript_validation_reports_exact_stage(monkeypatch, capture, data, reason):
    monkeypatch.setattr(gemini, "call_transcription_json", Mock(return_value=data))
    assert tr.transcribe(PRIVATE.encode(), "audio/ogg") is None
    assert_safe(capture, "transcript", reason)


def test_adapter_exception_is_private(monkeypatch, capture):
    monkeypatch.setattr(gemini, "call_transcription_json", Mock(side_effect=RuntimeError(PRIVATE)))
    assert tr.transcribe(b"audio", "audio/ogg") is None
    assert_safe(capture, "transcript", "adapter_exception")


def test_success_is_unchanged_and_emits_no_failure(monkeypatch, capture):
    data = {"transcript": PRIVATE, "readable": PRIVATE, "summary": ""}
    response = SimpleNamespace(candidates=[SimpleNamespace(finish_reason="STOP")], text=json.dumps(data), usage_metadata=None)
    generate = Mock(return_value=response)
    monkeypatch.setattr(gemini, "_get_client", lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    assert tr.transcribe(b"audio", "audio/ogg").literal == PRIVATE
    generate.assert_called_once()
    assert "transcription_failure" not in capture.text


@pytest.mark.parametrize("download,reason", [(Mock(return_value=None), "download_missing"),
                                           (Mock(side_effect=RuntimeError(PRIVATE)), "download_exception"),
                                           (Mock(return_value=(b"audio", "private/mime")), "unsupported_type")])
def test_webhook_audio_failure_is_safe_and_never_calls_provider(db_path, make_user, monkeypatch, capture, download, reason):
    from src.db import models
    provider = Mock(side_effect=AssertionError("must not call model"))
    monkeypatch.setattr(gemini, "call_transcription_json", provider)
    user = models.get_user_by_id(make_user())
    message = {"id": "synthetic-audio", "from": "972500000001", "type": "audio",
               "audio": {"id": "synthetic-media", "mime_type": "audio/ogg"}, "context": {"forwarded": True}}
    assert tr.handle(user, message, download=download, send=Mock(return_value=True))
    provider.assert_not_called()
    assert_safe(capture, "audio", reason)

"""Recover known JSON envelopes without inventing or dropping recorded speech."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import httpx
from google import genai
from google.genai import types

from src import transcription as tr
from src.ai import use_provider
from src.integrations import gemini

SPEECH = "לא למחוק את הפגישה. דנה אמרה דנה, 14:30 ביום שני.\nזה החלק האחרון, והוא חייב להישאר."


def call(monkeypatch, text, finish="STOP"):
    generate = Mock(return_value=SimpleNamespace(text=text, candidates=[SimpleNamespace(finish_reason=finish)], usage_metadata=None))
    monkeypatch.setattr(gemini, "_get_client", lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
    with use_provider("gemini"):
        result = tr.transcribe(b"synthetic-audio", "audio/ogg")
    generate.assert_called_once()
    return result, generate.call_args.kwargs["config"]


@pytest.mark.parametrize("wrapper", [lambda s: s, lambda s: " \n" + s + "\n ",
                                    lambda s: "```json\n" + s + "\n```",
                                    lambda s: "```JSON\r\n" + s + "\r\n```",
                                    lambda s: "```\n" + s + "\n```"])
def test_whole_valid_or_fenced_response_keeps_complete_literal(monkeypatch, wrapper):
    result, config = call(monkeypatch, wrapper(json.dumps({"transcript": SPEECH, "readable": SPEECH, "summary": ""})))
    assert result.literal == result.readable == SPEECH
    assert config.response_mime_type == "application/json"
    schema = config.response_json_schema
    assert schema["required"] == ["transcript", "readable", "summary"]
    assert all(p["type"] == "string" for p in schema["properties"].values())
    assert schema["additionalProperties"] is False
    # No schema text ceiling that could force the model to summarize/cut speech.
    assert "maxLength" not in schema["properties"]["transcript"]
    assert config.max_output_tokens == 16384 and config.http_options.timeout == 45000
    assert config.automatic_function_calling.disable is True


@pytest.mark.parametrize("wrapper", [lambda s: s, lambda s: "```json\n" + s + "\n```"])
def test_unescaped_newline_and_tab_inside_json_are_preserved(monkeypatch, wrapper):
    literal = "לא למחוק.\nדנה\tדנה 14:30. סוף ההקלטה."
    response = '{"transcript":"' + literal + '","readable":"' + literal + '","summary":""}'
    result, _ = call(monkeypatch, wrapper(response))
    assert result.literal == result.readable == literal


@pytest.mark.parametrize("text", [
    '{"transcript":"partial speech"',
    'prefix {"transcript":"speech"}',
    '{"transcript":"speech"} trailing prose',
    '```json\n{"transcript":"speech"}\n``` trailing',
    '{"transcript":"first","transcript":"different"}',
    '{"transcript":"speech","summary":NaN}',
    '{"transcript":"speech","summary":Infinity}',
    '{"transcript":"speech",}',
    '{"transcript":"line\nnext",}',
    '{"transcript":"line\nnext"} extra',
    '{"transcript":"speech\x00data"}',
    '[{"transcript":"speech"}]',
    None,
])
def test_other_malformed_or_partial_output_stays_rejected_without_leaking(monkeypatch, caplog, text):
    caplog.set_level("INFO", logger="uvicorn.error.transcription")
    result, _ = call(monkeypatch, text)
    assert result is None
    assert "transcription_failure" in caplog.text
    assert "speech" not in caplog.text and "different" not in caplog.text


def test_truncated_response_is_rejected_even_if_json_looks_complete(monkeypatch):
    result, _ = call(monkeypatch, '{"transcript":"partial speech"}', finish="MAX_TOKENS")
    assert result is None


def test_long_complete_speech_survives_fence_and_condensed_readable(monkeypatch):
    literal = "זו מילה חוזרת בלי מספרים. " * 400 + "זה סוף ההקלטה שאסור להשמיט."
    response = "```json\n" + json.dumps({"transcript": literal, "readable": "סיכום קצר.", "summary": "תקציר נפרד."}) + "\n```"
    result, _ = call(monkeypatch, response)
    assert result.literal == result.readable == literal
    assert result.summary == "תקציר נפרד."


def test_pinned_sdk_serializes_schema_into_request_without_network(monkeypatch):
    requests = []

    def reply(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [
            {"text": json.dumps({"transcript": SPEECH, "readable": SPEECH, "summary": ""})}]},
            "finishReason": "STOP"}], "modelVersion": "gemini-3.8-flash"})

    with genai.Client(api_key="synthetic-key", http_options=types.HttpOptions(
            client_args={"transport": httpx.MockTransport(reply)})) as client:
        monkeypatch.setattr(gemini, "_get_client", lambda: client)
        with use_provider("gemini"):
            result = tr.transcribe(b"synthetic-audio", "audio/ogg")
    assert result.literal == SPEECH
    assert len(requests) == 1
    generation = requests[0]["generationConfig"]
    assert generation["responseJsonSchema"] == gemini._TRANSCRIPTION_SCHEMA
    assert generation["responseMimeType"] == "application/json"
    assert generation["maxOutputTokens"] == 16384

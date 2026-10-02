"""
search_web (2026-09-14) - Gemini's Google Search grounding, verified live
once against the real API (not assumed from docs - see this session's
smoke_search_web.py, not checked in) to confirm the actual response shape:
grounding_chunks[].web.uri is a Google redirect/attribution link, not the
raw page URL, and .title carries the real site name. These tests fake that
exact shape rather than re-verifying it against the real, billed API.
"""
from unittest.mock import MagicMock

from src.integrations import gemini


class _FakeWeb:
    def __init__(self, title, uri):
        self.title = title
        self.uri = uri


class _FakeChunk:
    def __init__(self, title, uri):
        self.web = _FakeWeb(title, uri)


class _FakeGroundingMetadata:
    def __init__(self, chunks):
        self.grounding_chunks = chunks


class _FakeCandidate:
    def __init__(self, chunks):
        self.grounding_metadata = _FakeGroundingMetadata(chunks)


class _FakeResponse:
    def __init__(self, text, chunks=()):
        self.text = text
        self.usage_metadata = None  # short-circuits _log_usage_safe, no DB touched
        self.candidates = [_FakeCandidate(list(chunks))]


def test_search_web_returns_answer_and_deduplicated_sources(monkeypatch):
    chunks = [
        _FakeChunk("wunderground.com", "https://vertexaisearch.cloud.google.com/x1"),
        _FakeChunk("wunderground.com", "https://vertexaisearch.cloud.google.com/x1"),  # duplicate uri
        _FakeChunk("israelweather.co.il", "https://vertexaisearch.cloud.google.com/x2"),
    ]
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _FakeResponse("It's warm today.", chunks)
    monkeypatch.setattr(gemini, "_get_client", lambda: fake_client)

    result = gemini.search_web("weather in Tel Aviv")

    assert result["answer"] == "It's warm today."
    assert result["sources"] == [
        {"title": "wunderground.com", "uri": "https://vertexaisearch.cloud.google.com/x1"},
        {"title": "israelweather.co.il", "uri": "https://vertexaisearch.cloud.google.com/x2"},
    ]


def test_search_web_passes_the_google_search_tool(monkeypatch):
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _FakeResponse("answer")
    monkeypatch.setattr(gemini, "_get_client", lambda: fake_client)

    gemini.search_web("some query")

    kwargs = fake_client.models.generate_content.call_args.kwargs
    assert kwargs["contents"] == "some query"
    tools = kwargs["config"].tools
    assert len(tools) == 1
    assert tools[0].google_search is not None


def test_search_web_returns_none_on_api_failure(monkeypatch):
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = RuntimeError("network error")
    monkeypatch.setattr(gemini, "_get_client", lambda: fake_client)

    assert gemini.search_web("anything") is None


def test_search_web_returns_none_when_text_is_empty(monkeypatch):
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _FakeResponse("")
    monkeypatch.setattr(gemini, "_get_client", lambda: fake_client)

    assert gemini.search_web("anything") is None


def test_search_web_survives_missing_grounding_metadata(monkeypatch):
    """A real answer with no extractable sources (e.g. the SDK shape changes,
    or grounding genuinely found nothing) must still return the answer, not
    fail the whole call."""
    response = _FakeResponse("an answer with no sources")
    response.candidates = [MagicMock(grounding_metadata=None)]
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = response
    monkeypatch.setattr(gemini, "_get_client", lambda: fake_client)

    result = gemini.search_web("anything")
    assert result == {"answer": "an answer with no sources", "sources": []}


def test_search_web_caps_sources_at_five(monkeypatch):
    chunks = [_FakeChunk(f"site{i}.com", f"https://x/{i}") for i in range(8)]
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _FakeResponse("answer", chunks)
    monkeypatch.setattr(gemini, "_get_client", lambda: fake_client)

    result = gemini.search_web("anything")
    assert len(result["sources"]) == 5


def test_search_web_asks_for_a_hebrew_answer(monkeypatch):
    """The classifier may hand over an English-rephrased query; without this the
    grounded answer came back in English to a Hebrew question (seen live)."""
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _FakeResponse("answer")
    monkeypatch.setattr(gemini, "_get_client", lambda: fake_client)

    gemini.search_web("height of the Eiffel Tower")

    assert "Hebrew" in fake_client.models.generate_content.call_args.kwargs["config"].system_instruction

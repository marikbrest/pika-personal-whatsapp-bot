"""
Gemini API integration - calls that expect a JSON-only response.

Uses the current official SDK (`google-genai`) rather than the deprecated
`google-generativeai` (EOL 2025-11-30). The new SDK is the one Google
continues to develop and support for newer models (Gemini 3.x and later).

Also supports audio input (multimodal) - Gemini transcribes and classifies
intent in a single call, with no separate transcription service (used for
WhatsApp voice messages).

PRD 12.3 - retry policy: if Gemini returns malformed JSON, retry once. If the
error is a rate limit / quota error, do not retry immediately (that would only
compound the problem).
"""
from src.i18n import language_name_english
import json
import re

from google import genai
from google.genai import types

from src.ai import current_provider, get_adapter
from src.transcription_diagnostics import failure as transcription_failure, provider_exception_reason
from src.config import GEMINI_API_KEY, GEMINI_MODEL

MODEL_NAME = GEMINI_MODEL  # default "gemini-flash-latest": Google's alias for the current recommended flash
# model - avoids the deprecation trap where a hardcoded model name silently 404'd for the API key in use and the
# code's fail-open path masked it for days. Always verify the actual response
# body after changing this (a "success" HTTP status is not proof the model
# itself is still valid for this key).

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is missing from .env")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def _call_once(contents) -> str:
    """
    A single Gemini call, with response_mime_type=json to force valid JSON.

    thinking_level="low" matters here: the current flash model is a "thinking model" that
    generates hidden reasoning tokens before the answer itself. At the default
    (medium/high) this both slows every response noticeably and can exhaust the
    token budget on reasoning, truncating the JSON mid-object (observed in the
    logs as responses missing the closing "}"). For a short classification/JSON
    task like ours, "low" is sufficient and significantly faster.
    max_output_tokens is an extra safety net against truncation.
    """
    client = _get_client()
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=contents,
        config=types.GenerateContentConfig(
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_level="low"),
            max_output_tokens=2048,
        ),
    )
    _log_usage_safe(response)
    return response.text


def _log_usage_safe(response) -> None:
    """
    Records real token usage from the API's own usage_metadata (exact, not an
    estimate) for the admin usage/cost report. thoughts_token_count is
    counted as output since Google bills hidden reasoning tokens at the
    output rate. Never lets a logging failure affect the actual Gemini call -
    late import, same "non-fatal, log and continue" convention as
    models._embed_message_in_background.
    """
    try:
        usage = response.usage_metadata
        if usage is None:
            return
        from src.db.models import log_api_usage
        output_tokens = (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
        log_api_usage("gemini_generate", input_tokens=usage.prompt_token_count, output_tokens=output_tokens)
    except Exception as e:
        print(f"[gemini] usage logging failed (non-fatal): {e}")


def _truncate_for_log(text: str, max_chars: int = 200) -> str:
    """
    Privacy audit (2026-09-26): a malformed-JSON response can reconstruct/
    echo the user's own message content (e.g. an email draft body, a
    reminder's text) - printed only for debugging a parse failure, but this
    log is readable via the admin panel's /admin/logs. Bounding it keeps the
    diagnostic value (enough to see roughly what went wrong) without dumping
    a full private message into a shared log file.
    """
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"...[truncated, {len(text)} chars total]"


def _call_with_retry(contents) -> dict | None:
    """
    Retry logic shared by both the text and audio paths.
    Retries once if the response cannot be parsed. If the error is a rate limit
    or quota error, does not retry immediately.
    Returns None on failure (the caller must handle that gracefully).
    """
    try:
        text = _call_once(contents)
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            print(f"[gemini] first call succeeded but JSON parse failed: {_truncate_for_log(text)!r}")
    except Exception as e:
        error_text = str(e)
        if "quota" in error_text.lower() or "rate limit" in error_text.lower() or "429" in error_text:
            print(f"[gemini] rate limit / quota exceeded - not retrying immediately: {e}")
            return None
        print(f"[gemini] first call failed: {e}")

    # Second attempt - only for malformed JSON (rate limits already returned above)
    try:
        text = _call_once(contents)
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            print(f"[gemini] retry also failed to parse: {_truncate_for_log(text)!r}")
    except Exception as e:
        print(f"[gemini] retry failed: {e}")

    return None


def call_gemini_json(prompt: str) -> dict | None:
    """Sends a text prompt to the current user's AI provider (Gemini unless they chose another) and expects JSON."""
    if current_provider() != "gemini":
        return get_adapter(current_provider()).call_json(prompt)
    return _call_with_retry(prompt)


def search_web(query: str) -> dict | None:
    """
    Answers a question using Gemini's built-in Google Search grounding - a
    real web search with real, citable sources, not just the model's own
    (possibly stale or invented) training knowledge.

    Deliberately NOT built on call_gemini_json/_call_with_retry: Gemini's
    tool use (the google_search tool this requires) and forced
    response_mime_type="application/json" are mutually exclusive - trying
    both together is rejected by the API. This is its own call shape, free
    text with a separate grounding_metadata field, not a JSON variant.

    Verified live against the real API (not assumed from docs): the sources
    Gemini returns in grounding_chunks[].web are NOT the original page URLs -
    they're Google's own redirect/attribution links
    (vertexaisearch.cloud.google.com/grounding-api-redirect/...) that forward
    to the real page. That's expected grounding behaviour, not a bug - the
    .title field (e.g. "wunderground.com") is what actually tells the reader
    which real site it came from, so it's carried alongside the link.

    Returns {"answer": str, "sources": [{"title": str, "uri": str}, ...]}
    (sources deduplicated by uri, capped at 5, in citation order), or None on
    total failure.
    """
    if current_provider() != "gemini":
        return get_adapter(current_provider()).search_web(query)

    client = _get_client()
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=query,
            config=types.GenerateContentConfig(
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                # The classifier may rephrase the query in English; the reply
                # still has to come back in the user's language.
                system_instruction=f"Answer in {language_name_english()}, concisely, as a WhatsApp message.",
                tools=[types.Tool(google_search=types.GoogleSearch())],
                thinking_config=types.ThinkingConfig(thinking_level="low"),
                max_output_tokens=2048,
            ),
        )
    except Exception as e:
        print(f"[gemini] search_web call failed: {e}")
        return None

    _log_usage_safe(response)

    answer = response.text
    if not answer:
        return None

    sources = []
    try:
        chunks = response.candidates[0].grounding_metadata.grounding_chunks or []
        seen_uris = set()
        for chunk in chunks:
            web = getattr(chunk, "web", None)
            uri = getattr(web, "uri", None)
            if uri and uri not in seen_uris:
                seen_uris.add(uri)
                sources.append({"title": getattr(web, "title", None) or uri, "uri": uri})
    except Exception as e:
        print(f"[gemini] could not extract grounding sources (non-fatal, answer still returned): {e}")

    return {"answer": answer, "sources": sources[:5]}


def call_gemini_json_with_media(prompt: str, media_bytes: bytes, mime_type: str) -> dict | None:
    """
    Sends a prompt plus a media file to Gemini in one multimodal call and expects
    JSON. Used for voice messages (transcription and intent classification in a
    single step, with no separate transcription service) and for images and PDFs.
    """
    if current_provider() != "gemini":
        return get_adapter(current_provider()).call_json_with_media(prompt, media_bytes, mime_type)

    media_part = types.Part.from_bytes(data=media_bytes, mime_type=mime_type)
    return _call_with_retry([prompt, media_part])


# Media types Gemini can read inline. Anything else (Word, Excel, zip, ...) has
# to be refused explicitly rather than sent and silently misinterpreted.
SUPPORTED_MEDIA_MIME_TYPES = {
    "image/png", "image/jpeg", "image/webp", "image/heic", "image/heif",
    "application/pdf",
    "audio/ogg", "audio/mpeg", "audio/mp4", "audio/wav", "audio/webm", "audio/aac",
}

# Inline request payloads are capped at roughly 20 MB by the API. Refuse earlier
# with a clear message instead of letting the call fail opaquely.
MAX_MEDIA_BYTES = 15 * 1024 * 1024


def is_supported_media(mime_type: str) -> bool:
    """True if Gemini can read this media type inline."""
    return mime_type.split(";")[0].strip().lower() in SUPPORTED_MEDIA_MIME_TYPES


EMBEDDING_MODEL_NAME = "gemini-embedding-001"


def embed_content(text: str) -> list[float]:
    """
    Returns the raw embedding vector for a piece of text, for semantic search
    (src/integrations/embeddings.py handles byte serialization and similarity
    math - this function is just the API call, same separation of concerns as
    the rest of this module).
    """
    client = _get_client()
    response = client.models.embed_content(model=EMBEDDING_MODEL_NAME, contents=text)

    try:
        # Unlike generate_content, the embedding API exposes no usage
        # metadata at all (confirmed empirically - no usage_metadata,
        # metadata, or per-embedding statistics field returns anything), so
        # this is an estimate (~4 chars/token, a standard rough heuristic),
        # not an exact count like gemini_generate's numbers.
        from src.db.models import log_api_usage
        log_api_usage("gemini_embed", input_tokens=max(1, len(text) // 4), output_tokens=None)
    except Exception as e:
        print(f"[gemini] embed usage logging failed (non-fatal): {e}")

    return response.embeddings[0].values


_TRANSCRIPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "transcript": {"type": "string", "description": "Complete literal speech, including every word and repetition; never a summary."},
        "readable": {"type": "string", "description": "The same complete words in the same order; only punctuation and whitespace may change."},
        "summary": {"type": "string", "description": "A separate short summary in the spoken language; empty when unavailable."},
    },
    "required": ["transcript", "readable", "summary"],
    "additionalProperties": False,
}


def _transcription_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_json_constant(value):
    raise ValueError("nonfinite_json_constant")


def _load_transcription_json(text):
    """Parse the whole response; never extract a partial object or repair speech."""
    if not isinstance(text, str):
        raise ValueError("missing_json_text")
    text = text.strip()
    # Some providers wrap a complete JSON object in Markdown despite JSON mode.
    # Only a single whole-response fence is accepted; surrounding prose is rejected.
    fenced = re.fullmatch(r"```(?:json)?[ \t]*\r?\n(.*)\r?\n```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    options = {"object_pairs_hook": _transcription_object, "parse_constant": _reject_json_constant}
    try:
        return json.loads(text, **options)
    except json.JSONDecodeError as exc:
        # A literal newline/tab inside a quoted transcript is unescaped JSON, but
        # it is still speech data. Preserve it exactly; do not escape/alter words.
        # Other syntax errors, missing endings and trailing prose stay rejected.
        if not exc.msg.startswith("Invalid control character"):
            raise
        if any(ord(char) < 32 and char not in "\t\n\r" for char in text):
            raise
        return json.loads(text, strict=False, **options)


def call_transcription_json(prompt: str, media_bytes: bytes, mime_type: str) -> dict | None:
    """Dedicated bounded audio path: no content/exception logs or automatic retry."""
    if current_provider() == "openai":
        from src.integrations.openai import call_transcription_json as selected_call
        return selected_call(prompt, media_bytes, mime_type)
    try:
        response = _get_client().models.generate_content(
            model=MODEL_NAME, contents=[prompt, types.Part.from_bytes(data=media_bytes, mime_type=mime_type)],
            config=types.GenerateContentConfig(
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                response_mime_type="application/json", thinking_config=types.ThinkingConfig(thinking_level="low"),
                response_json_schema=_TRANSCRIPTION_SCHEMA,
                max_output_tokens=16384, http_options=types.HttpOptions(timeout=45000),
            ),
        )
        try:
            from src.db.models import log_ai_usage
            u = response.usage_metadata
            if u is not None:
                log_ai_usage("gemini", getattr(response, "model_version", None) or MODEL_NAME,
                             u.prompt_token_count or 0, (u.candidates_token_count or 0) + (u.thoughts_token_count or 0),
                             u.cached_content_token_count or 0)
        except Exception:
            pass
        if not response.candidates:
            transcription_failure("gemini", "no_candidates")
            return None
        reason = response.candidates[0].finish_reason
        if reason != "STOP":
            code = "output_limit" if reason == "MAX_TOKENS" else "safety_block" if reason == "SAFETY" else "finish_not_stop"
            transcription_failure("gemini", code)
            return None
        try:
            result = _load_transcription_json(response.text)
        except (ValueError, TypeError):
            transcription_failure("gemini", "invalid_json")
            return None
        if not isinstance(result, dict):
            transcription_failure("gemini", "invalid_shape")
            return None
        return result
    except Exception as exc:
        transcription_failure("gemini", provider_exception_reason(exc))
        return None

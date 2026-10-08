"""Closed diagnostic vocabulary: never log audio, model text or exception details."""
import logging

import httpx

logger = logging.getLogger("uvicorn.error.transcription")
_REASONS = {
    "audio": {"invalid_input", "unsupported_type", "too_large", "download_missing", "download_exception"},
    "gemini": {"no_candidates", "output_limit", "safety_block", "finish_not_stop", "invalid_json",
               "invalid_shape", "timeout", "connection_error", "rate_limit", "authentication",
               "permission", "request_rejected", "provider_unavailable", "provider_exception"},
    "transcript": {"adapter_exception", "no_result", "invalid_shape", "missing_text", "empty_text", "text_too_long"},
}


def failure(stage, reason):
    # Even accidentally passing untrusted model/error data cannot put it in a log.
    safe_stage = stage if isinstance(stage, str) and stage in _REASONS else "unknown"
    safe_reason = (reason if isinstance(reason, str) and reason in _REASONS.get(safe_stage, ()) else "unknown")
    logger.info("transcription_failure stage=%s reason=%s", safe_stage, safe_reason)


def provider_exception_reason(exc):
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    if isinstance(exc, httpx.TransportError):
        return "connection_error"
    code = getattr(exc, "code", None)
    if type(code) is int:
        return {400: "request_rejected", 401: "authentication", 403: "permission", 404: "request_rejected",
                429: "rate_limit", 500: "provider_unavailable", 502: "provider_unavailable",
                503: "provider_unavailable", 504: "provider_unavailable"}.get(code, "provider_exception")
    return "provider_exception"

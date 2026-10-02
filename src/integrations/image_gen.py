"""
Image generation and editing via Gemini's native image model (2026-09-26).
the admin asked for two things: "create me an image of..." (generate_image) and
editing a photo the user sends (edit_image).

Uses the SAME client.models.generate_content call shape as the rest of
src/integrations/gemini.py, for consistency, rather than the newer
client.interactions API the current docs also show - both work, but keeping
one call pattern across the whole codebase is worth more than being on the
very latest surface.

Model choice verified live against the real API key in use (not assumed from
docs, same discipline as gemini.py's MODEL_NAME comment): both
"gemini-2.5-flash-image" and "gemini-3-pro-image-preview" produced real image
bytes for this key. Picked the non-preview flash model - faster/cheaper,
fitting a low-traffic personal bot, and "-preview" model ids have already bit
this codebase once (see gemini.py's own MODEL_NAME comment about a hardcoded
name silently 404ing).
"""
from google.genai import types

from src.integrations.gemini import _get_client, _log_usage_safe

IMAGE_MODEL_NAME = "gemini-2.5-flash-image"


def _extract_image(response) -> tuple[bytes, str] | None:
    """
    Pulls the first inline image out of a generate_content response.
    response_modalities=["TEXT", "IMAGE"] means the model can return both a
    text part (e.g. a short caption) and an image part - only the image
    matters to callers here, the text (if any) is not currently surfaced.
    """
    try:
        parts = response.candidates[0].content.parts
    except (AttributeError, IndexError, TypeError):
        return None
    for part in parts or []:
        inline_data = getattr(part, "inline_data", None)
        if inline_data is not None and inline_data.data:
            return inline_data.data, inline_data.mime_type or "image/png"
    return None


def generate_image(prompt: str) -> tuple[bytes, str] | None:
    """
    Generates a brand-new image from a text prompt. Returns (image_bytes,
    mime_type), or None on failure - callers must handle that gracefully
    (same "never raise past the integration boundary" convention as the rest
    of this codebase's Gemini calls).
    """
    client = _get_client()
    try:
        response = client.models.generate_content(
            model=IMAGE_MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"], automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)),
        )
    except Exception as e:
        print(f"[image_gen] generate_image failed: {e}")
        return None
    _log_usage_safe(response)
    return _extract_image(response)


def edit_image(image_bytes: bytes, mime_type: str, instructions: str) -> tuple[bytes, str] | None:
    """
    Edits an existing image per a text instruction (e.g. "make it black and
    white", "add a hat"). Same model as generate_image - it is natively
    multimodal, so an input image plus a text instruction is just another
    `contents` list, the same shape gemini.call_gemini_json_with_media
    already uses for reading (not generating) images elsewhere in this
    codebase. Returns (image_bytes, mime_type), or None on failure.
    """
    client = _get_client()
    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    try:
        response = client.models.generate_content(
            model=IMAGE_MODEL_NAME,
            contents=[instructions, image_part],
            config=types.GenerateContentConfig(response_modalities=["TEXT", "IMAGE"], automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)),
        )
    except Exception as e:
        print(f"[image_gen] edit_image failed: {e}")
        return None
    _log_usage_safe(response)
    return _extract_image(response)

"""
The ONLY file that knows google.genai's types - registry.py and every
tools/*.py module stay provider-neutral. See registry.py's module docstring
for the two live-verified decisions this implements (ANY mode; reply_text as
a schema argument instead of a second round-trip).
"""
from google.genai import types

from src.integrations.gemini import MODEL_NAME, _get_client, _log_usage_safe
from src.tools.registry import Tool


def _to_function_declaration(tool: Tool) -> types.FunctionDeclaration:
    return types.FunctionDeclaration(
        name=tool.name,
        description=tool.description,
        parameters_json_schema=tool.parameters,
    )


def classify_with_tools(contents, tools: list[Tool]) -> tuple[str, dict] | None:
    """
    One Gemini call, forced (ANY) to pick exactly one of `tools`. Returns
    (tool_name, args) on success, or None on total failure (no retry here -
    see the note below on why that differs from call_gemini_json).

    thinking_level="low": same reasoning as gemini.py's call_gemini_json -
    this is a classification task, not one that benefits from extended
    reasoning, and the "low" setting is what avoids the truncation failure
    mode already observed and documented there.

    No retry-on-malformed-JSON here unlike call_gemini_json: function
    calling is schema-validated by the API itself before it ever reaches us
    (the SDK raises on a genuinely malformed call), so the failure modes
    that justified a retry in the free-text JSON path (occasional truncated
    JSON) do not apply the same way here. A real API/network failure still
    surfaces as an exception, caught below and treated as "no result" -
    callers fall back exactly as they do today when Gemini fails.
    """
    declarations = [_to_function_declaration(t) for t in tools]
    tool_names = [t.name for t in tools]

    try:
        client = _get_client()
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=contents,
            config=types.GenerateContentConfig(
                tools=[types.Tool(function_declarations=declarations)],
                # Tool calls are executed by our own dispatcher, never by the SDK.
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                tool_config=types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode="ANY", allowed_function_names=tool_names,
                    )
                ),
                thinking_config=types.ThinkingConfig(thinking_level="low"),
                max_output_tokens=2048,
            ),
        )
    except Exception as e:
        print(f"[tools] classify_with_tools call failed: {e}")
        return None

    _log_usage_safe(response)

    if not response.function_calls:
        # Privacy audit (2026-09-26): never repr() the full response - it can
        # carry response.text, which reconstructs/echoes the user's own
        # message content, and this log is readable via /admin/logs.
        # finish_reason alone is enough to diagnose why ANY mode came back empty.
        finish_reason = None
        try:
            finish_reason = response.candidates[0].finish_reason
        except (AttributeError, IndexError):
            pass
        print(f"[tools] ANY mode returned no function call - unexpected (finish_reason={finish_reason})")
        return None

    call = response.function_calls[0]
    return call.name, dict(call.args or {})

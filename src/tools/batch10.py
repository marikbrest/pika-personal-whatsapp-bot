"""
Batch 10 (2026-09-14): explain_capabilities - "what can you do" / "help".
the admin's own framing was "accurate AND simple", which is exactly what ruled
out just letting this fall through to 'chat': a free-form Gemini answer
about its own capabilities risks listing something the bot doesn't actually
do, or missing something real. _handle_explain_capabilities (webhook_handler.py)
builds the reply from a maintained, grouped Hebrew mapping keyed by real
tool name, checked live against tools_for(user) - see its own docstring and
_CAPABILITY_GROUPS for the full reasoning.
"""
from src.tools.registry import Tool, register
from src.webhook_handler import _handle_explain_capabilities

explain_capabilities_tool = register(Tool(
    name="explain_capabilities",
    description=(
        "Explains, in simple everyday language, what the bot can actually do. Use this for "
        "anything like 'what can you do', 'help', 'מה אתה יודע לעשות', 'עזרה', 'איך אתה יכול "
        "לעזור לי', 'מה האפשרויות שלך'. Do NOT use 'chat' for this - the answer must come from "
        "the bot's real, grounded capability list, not a free-form guess."
    ),
    parameters={"type": "object", "properties": {}},
    handler=lambda user, args: _handle_explain_capabilities(user),
))

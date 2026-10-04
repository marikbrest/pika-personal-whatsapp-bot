"""Self-service AI provider selection (no API key is ever supplied by a user)."""
from src.ai import PROVIDERS, manage_provider
from src.tools.registry import Tool, register

manage_ai_provider_tool = register(Tool(
    name="manage_ai_provider",
    description=(
        "Choose which AI provider (" + ", ".join(s.label for s in PROVIDERS.values()) + ") handles this user's conversations "
        "and proactive notifications, or show the current provider and model. Use for 'switch to OpenAI', 'go back to "
        "Gemini', 'which model am I using', 'עבור ל־OpenAI', 'חזור ל־Gemini', 'באיזה מודל אני משתמש'. "
        "Only ever changes the requesting user's own setting."
    ),
    parameters={"type": "object", "properties": {
        "action": {"type": "string", "enum": ["set", "status"]},
        "provider": {"type": "string", "enum": list(PROVIDERS)},
    }, "required": ["action"]},
    handler=manage_provider,
    validate=lambda args: args.get("action") == "status" or (args.get("action") == "set" and args.get("provider") in PROVIDERS),
))

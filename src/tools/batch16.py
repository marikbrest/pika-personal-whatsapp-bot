"""
New feature (2026-09-26): the privacy half of the isolation review the
admin asked for. explain_privacy gives a grounded, accurate answer (never a
free-form Gemini guess - see _handle_explain_privacy's own docstring) when
a family member asks whether the admin can see their messages/emails.
manage_my_data is the self-service backing for that answer - "show me
what you have on me" / "delete my history" - so the privacy explanation
isn't just words, it's something anyone can actually act on themselves.
"""
from src.tools.registry import Tool, register
from src.webhook_handler import _handle_explain_privacy, _handle_manage_my_data

explain_privacy_tool = register(Tool(
    name="explain_privacy",
    description=(
        "The user asks about privacy - whether the admin/developer can see their messages, "
        "emails, or conversation content, or a general 'is my data private / who can see this' "
        "question ('האם המנהל רואה את ההודעות שלי', 'זה פרטי?', 'מי יכול לראות מה אני כותב', "
        "'is my data private'). Always use this dedicated tool for privacy questions - the answer must "
        "come from real, grounded code (do NOT answer from 'chat' or general knowledge, which risks "
        "giving an inaccurate or inconsistent answer about something that genuinely matters)."
    ),
    parameters={"type": "object", "properties": {}},
    handler=lambda user, args: _handle_explain_privacy(),
))


def _validate_manage_my_data_args(args: dict) -> bool:
    return args.get("action") in ("show", "delete_history")


manage_my_data_tool = register(Tool(
    name="manage_my_data",
    description=(
        "Shows the user a summary of what data is stored about them (action=show - message count, "
        "active reminders, saved contacts/links, remembered facts, Google connection status - counts "
        "only, never the raw content itself), or deletes their own conversation history (action="
        "delete_history - removes only their message log, not their reminders/tasks/contacts/facts). "
        "Use for requests like 'תראה לי מה יש עליי', 'מה אתה יודע עליי', 'תמחק את ההיסטוריה שלי', "
        "'show me what you have on me', 'delete my history'. This only ever affects the requesting "
        "user's own data - never use it for anyone else."
    ),
    parameters={
        "type": "object",
        "properties": {"action": {"type": "string", "enum": ["show", "delete_history"]}},
        "required": ["action"],
    },
    handler=lambda user, args: _handle_manage_my_data(user, args),
    validate=_validate_manage_my_data_args,
))

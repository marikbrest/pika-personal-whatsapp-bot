"""
Batch 5 of the function-calling migration (2026-09-14): user_manage,
zabbix_status, usage_status, email_read. The first batch to register
admin_only=True tools - tools_for() already excludes them from a non-admin
user's classification entirely (registry.py's own defense-in-depth design),
and every handler here ALSO independently re-checks user["is_admin"] itself
(the same second layer every one of these handlers already had before this
migration touched them) - a bug in tools_for() filtering must never become a
privilege escalation on its own.

email_read is the one non-admin tool in this batch - included because it's
otherwise exactly the same shape as every other batch (renders_own_reply=
True, no pending state) and its boundary against analyze_email (batch 2) is
worth completing as an explicit pair now that both sides exist as real tools.
"""
from src.tools.registry import Tool, register
from src.webhook_handler import (
    _handle_email_read,
    _handle_usage_status,
    _handle_user_manage,
    _handle_zabbix_status,
)

zabbix_status_tool = register(Tool(
    name="get_infra_status",
    description=(
        "Admin-only home-infrastructure status: Zabbix monitoring, UniFi network, and Firewalla "
        "firewall - whether everything at home is healthy, any alerts, device/connection counts. "
        "Do NOT use this for the bot's own API usage/cost report (use get_usage_status) - this is "
        "specifically about home network/server health, nothing to do with the bot's own running costs."
    ),
    parameters={"type": "object", "properties": {}},
    handler=lambda user, args: _handle_zabbix_status(user),
    admin_only=True,
))

usage_status_tool = register(Tool(
    name="get_usage_status",
    description=(
        "Admin-only report of the bot's own API usage and cost - real Gemini token counts, "
        "estimated embedding cost, and Ship24 tracking-quota usage this month. Do NOT use this "
        "for home network/server monitoring (use get_infra_status) - this is specifically about "
        "what running the bot itself costs, nothing to do with home infrastructure health."
    ),
    parameters={"type": "object", "properties": {}},
    handler=lambda user, args: _handle_usage_status(user),
    admin_only=True,
))


def _validate_user_manage_args(args: dict) -> bool:
    """Transplanted verbatim from intent_parser._validate_result's user_manage block."""
    action = args.get("action")
    if action not in ("list", "add", "disable"):
        return False
    if action in ("add", "disable") and not args.get("whatsapp_number"):
        return False
    return True


user_manage_tool = register(Tool(
    name="manage_bot_users",
    description=(
        "Admin-only: grants or revokes permission to USE THE BOT ITSELF (the allowlist - the only "
        "thing standing between a stranger and the bot's Gemini quota), or lists current bot "
        "users. Do NOT use this for saving a personal contact's phone number so reminders can be "
        "sent to them (use add_contact instead, the default for an ordinary 'add contact' "
        "request) - only use this tool when the user EXPLICITLY says something about giving "
        "someone access/permission to the BOT itself, e.g. 'give X access to the bot', 'let X use "
        "this'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "add", "disable"]},
            "whatsapp_number": {
                "type": "string",
                "description": (
                    "Only for action=add/disable: international format, digits only, no '+'. "
                    "Convert an Israeli local number (05X-XXXXXXX) to 972XXXXXXXXX."
                ),
            },
            "display_name": {
                "type": "string",
                "description": "Only for action=add: an English display name, if one was given. Omit otherwise.",
            },
        },
        "required": ["action"],
    },
    handler=lambda user, args: _handle_user_manage(user, args),
    validate=_validate_user_manage_args,
    admin_only=True,
))

email_read_tool = register(Tool(
    name="read_emails",
    description=(
        "Lists recent emails (sender, subject, short snippet) - optionally filtered by a Gmail "
        "search query (e.g. unread only, from a specific sender). Do NOT use this for a deep "
        "summary of one specific thread or its commitments/deadlines, or for checking which sent "
        "emails are still unanswered (use analyze_email for either of those) - this tool only "
        "ever shows a short list of recent messages, never analyzes one in depth."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Gmail search syntax if relevant: 'is:unread', 'from:X'. Omit for all recent mail.",
            },
            "max_results": {
                "type": "integer",
                "description": "How many emails to show, default 5, max 10.",
            },
        },
        "required": [],
    },
    handler=lambda user, args: _handle_email_read(user, args),
))

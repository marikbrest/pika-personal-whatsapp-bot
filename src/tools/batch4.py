"""
Batch 4 of the function-calling migration (2026-09-14): reminder,
add_contact, connect_google - the first tools in this migration that need
Gemini's own generated sentence as (part of) the reply, not a purely
code-computed one.

Design note - a deliberate departure from Stage A's original plan: the
registry docstring anticipated a renders_own_reply=False path (reply is
ALWAYS exactly args["reply_text"], handler's return value ignored) for
exactly these three. That fits add_contact perfectly, but not the other two:
reminder needs to return a DIFFERENT, code-computed message when the named
recipient isn't a saved contact yet (Gemini cannot know that at
classification time), and connect_google needs to APPEND a real auth URL
after Gemini's sentence, not replace it. Neither fits "reply is always
exactly reply_text, no way to override or extend it". All three are
therefore renders_own_reply=True instead, with "reply_text" simply a normal
schema field the handler chooses to use, compose with, or override - still
exactly the "Gemini writes the sentence in the same call, no second
round-trip" property Stage A wanted, just without the rigid dispatch
shortcut that only actually fits one of the three tools.

Handlers are thin: _handle_reminder/_handle_add_contact/_handle_connect_google
were extracted from webhook_handler.py's own inline dispatch logic the same
day (not reimplemented), so the old classifier's "reminder"/"add_contact"/
"connect_google" intents and these tools share one real implementation each.
"""
from src.intent_parser import FALLBACK_REPLY
from src.tools.registry import Tool, register
from src.webhook_handler import _handle_add_contact, _handle_connect_google, _handle_reminder


def _validate_reminder_args(args: dict) -> bool:
    """Transplanted verbatim from intent_parser._validate_result's reminder block."""
    return {"content", "schedule_type", "schedule_time"}.issubset(args.keys())


reminder_tool = register(Tool(
    name="create_reminder",
    description=(
        "Creates a scheduled reminder (once, daily, or weekly) for the user themselves or for an "
        "existing contact by name. Do NOT use this for a calendar event/meeting with attendees or "
        "a location (use manage_calendar) and do NOT use this for a shopping/to-do list item with "
        "no specific time attached (use manage_tasks) - a reminder is specifically about being "
        "notified at a particular time."
    ),
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "Short description of what the reminder is about."},
            "schedule_type": {"type": "string", "enum": ["once", "daily", "weekly"]},
            "schedule_time": {
                "type": "string",
                "description": (
                    "For once: full future ISO datetime 'YYYY-MM-DDTHH:MM:SS', no timezone. "
                    "For daily/weekly: 'HH:MM'."
                ),
            },
            "schedule_days": {
                "type": "string",
                "description": (
                    "Only for weekly: comma-separated days from mon,tue,wed,thu,fri,sat,sun. "
                    "Omit entirely otherwise."
                ),
            },
            "recipient_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Existing contacts' names, each exactly as saved, only if this reminder is for "
                    "someone else - include EVERY name mentioned (e.g. 'תזכורת לדני, נועה ותומר' -> "
                    "all 3 names, not just the first). Omit entirely for a reminder to the user "
                    "themselves. One identical reminder is created per name."
                ),
            },
            "reply_text": {
                "type": "string",
                "description": "A short, friendly confirmation in the same language the user wrote in, saying what was set and for whom (name everyone if there is more than one recipient).",
            },
        },
        "required": ["content", "schedule_type", "schedule_time", "reply_text"],
    },
    handler=lambda user, args: _handle_reminder(user, args, args.get("reply_text") or FALLBACK_REPLY),
    validate=_validate_reminder_args,
))


def _validate_add_contact_args(args: dict) -> bool:
    """Transplanted verbatim from intent_parser._validate_result's add_contact block."""
    return {"name", "whatsapp_number"}.issubset(args.keys())


add_contact_tool = register(Tool(
    name="add_contact",
    description=(
        "Saves a new contact's name and phone number to the user's personal contact book, so "
        "reminders can later be sent to them by name. This is just a phonebook entry and does "
        "NOT grant the contact any access to the bot itself - default to this tool for an "
        "ordinary 'add contact X, number Y' request. Only decline (use 'chat' instead) when the "
        "user EXPLICITLY asks to give someone permission/access to USE THE BOT ITSELF (e.g. 'give "
        "access to...', 'let X use the bot') - that is a separate, admin-only feature this tool "
        "must never be used for."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Contact's name, transliterated to Latin letters if given in Hebrew (e.g. 'אמא' -> 'Mom').",
            },
            "whatsapp_number": {
                "type": "string",
                "description": (
                    "International format, digits only, no '+'. Convert an Israeli local number "
                    "(05X-XXXXXXX) to 972XXXXXXXXX."
                ),
            },
            "reply_text": {"type": "string", "description": "A short confirmation that the contact was saved."},
        },
        "required": ["name", "whatsapp_number", "reply_text"],
    },
    handler=lambda user, args: _handle_add_contact(user, args, args.get("reply_text") or FALLBACK_REPLY),
    validate=_validate_add_contact_args,
))

connect_google_tool = register(Tool(
    name="connect_google",
    description=(
        "Handles a request to connect or link the user's Gmail/Google Calendar/Drive account, in "
        "any phrasing or language ('connect my gmail', 'link my calendar', 'תחבר לי את היומן')."
    ),
    parameters={
        "type": "object",
        "properties": {
            "reply_text": {
                "type": "string",
                "description": (
                    "A short friendly sentence saying a connection link is coming. Do NOT write a "
                    "link yourself - the real one is appended automatically by code after your reply."
                ),
            },
        },
        "required": ["reply_text"],
    },
    handler=lambda user, args: _handle_connect_google(user, args.get("reply_text") or FALLBACK_REPLY),
))

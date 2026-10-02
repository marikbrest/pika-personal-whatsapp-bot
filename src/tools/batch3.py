"""
Batch 3 of the function-calling migration (2026-09-14): calendar, saved_link,
memory, morning_brief, semantic_search. Same "renders_own_reply=True, no
admin_only, no pending state" selection criteria as batches 1-2 - reminder,
add_contact, and connect_google are deliberately excluded here because they
rely on Gemini's own generated sentence as the reply (renders_own_reply=
False), a pattern this migration has designed for but never actually used in
a registered tool yet; better as its own focused batch 4 than mixed in here.

This batch has more inter-tool ambiguity than batch 2: three tools can all
plausibly be reached by "save/remember this" (saved_link, memory,
manage_drive), two by "watch/track a URL" (saved_link, manage_watches), and
two by "search" (semantic_search, web_search). Every description below
explicitly names its boundary against every other tool it could be confused
with, not just the old-classifier intents - the same lesson as batch 1/2,
applied one level wider this time.
"""
from src.tools.registry import Tool, register
from src.webhook_handler import (
    _handle_calendar,
    _handle_memory,
    _handle_morning_brief,
    _handle_saved_link,
    _handle_semantic_search,
)


def _validate_calendar_args(args: dict) -> bool:
    """Transplanted verbatim from intent_parser._validate_result's calendar block."""
    action = args.get("action")
    if action not in ("query", "create", "update"):
        return False
    if action == "query" and not {"start", "end"}.issubset(args.keys()):
        return False
    if action == "create" and not {"start", "end", "summary"}.issubset(args.keys()):
        return False
    if action == "update":
        if not args.get("match"):
            return False
        if not (args.get("start") or args.get("end") or args.get("summary")):
            return False
    return True


calendar_tool = register(Tool(
    name="manage_calendar",
    description=(
        "Google Calendar: view events in a time range, create a new event (including blocking "
        "focus time - give it a summary like 'Focus time'), or move/rename an existing event. "
        "Do NOT use this for a reminder that isn't a real calendar event with attendees/location "
        "(e.g. 'remind me tomorrow at 10 to call the doctor') - use 'chat' to let that fall "
        "through to the real reminder handler."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["query", "create", "update"]},
            "start": {
                "type": "string",
                "description": (
                    "For query: start of the range, full ISO 'YYYY-MM-DDTHH:MM:SS', no timezone. "
                    "For create: the event's start time. For update: a new start time, only if "
                    "moving the event - otherwise omit."
                ),
            },
            "end": {
                "type": "string",
                "description": (
                    "For query: end of the range. For create: the event's end time (assume 1 hour "
                    "if no duration given). For update: a new end time, only if an explicit new "
                    "duration was given - usually omit and let the original duration carry over."
                ),
            },
            "summary": {
                "type": "string",
                "description": "For create: a short event title. For update: a new title, only if renaming.",
            },
            "match": {
                "type": "string",
                "description": "Only for update: an identifying description of which existing event, by its title.",
            },
            "attendee_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Only for action=create: names of people mentioned as attending/coming, exactly "
                    "as said (e.g. 'שיוסלה ותמר באים אלינו' -> ['יוסלה', 'תמר']). Omit entirely if no "
                    "one specific is named. If a name belongs to someone who is ALSO a user of this "
                    "bot (a family member, not just a saved contact), the event is created on their "
                    "own calendar too, not just the requester's - found live 2026-09-26: a user "
                    "scheduled a meeting naming another family member and that other person's own "
                    "calendar stayed completely empty, with no indication anything had happened."
                ),
            },
        },
        "required": ["action"],
    },
    handler=lambda user, args: _handle_calendar(user, args),
    validate=_validate_calendar_args,
))


def _validate_saved_link_args(args: dict) -> bool:
    """Transplanted verbatim from intent_parser._validate_result's saved_link block."""
    action = args.get("action")
    if action not in ("save", "list", "forget"):
        return False
    if action == "save" and not args.get("url"):
        return False
    if action == "forget" and not args.get("match"):
        return False
    return True


saved_link_tool = register(Tool(
    name="manage_saved_links",
    description=(
        "Saves a PERMANENT snapshot of a URL's content (only when the user explicitly asks to "
        "save it, never automatically just because a link was sent), or lists/forgets previously "
        "saved links. Do NOT use this for a Google Drive file/note (use manage_drive) and do NOT "
        "use this if the user wants to be notified when a page CHANGES later (that is "
        "manage_watches, a completely different, ongoing feature) - this is a one-time permanent "
        "copy of the page as it is right now."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["save", "list", "forget"]},
            "url": {"type": "string", "description": "Only for action=save: the URL the user sent."},
            "match": {
                "type": "string",
                "description": "Only for action=forget: an identifying description of which saved link.",
            },
        },
        "required": ["action"],
    },
    handler=lambda user, args: _handle_saved_link(user, args),
    validate=_validate_saved_link_args,
))


def _validate_memory_args(args: dict) -> bool:
    """Transplanted verbatim from intent_parser._validate_result's memory block."""
    action = args.get("action")
    if action not in ("save", "list", "forget", "forget_all"):
        return False
    if action == "save" and not (args.get("fact_key") and args.get("fact_value")):
        return False
    if action == "forget" and not args.get("fact_key"):
        return False
    return True


memory_tool = register(Tool(
    name="manage_memory",
    description=(
        "Explicit long-term memory ONLY - saving, listing, or forgetting a personal fact about "
        "the user that they EXPLICITLY asked you to remember (e.g. 'remember that I'm "
        "vegetarian'). Do NOT use this for a shopping/to-do list item (use manage_tasks), for "
        "saving a URL (use manage_saved_links), or for anything the user did not explicitly ask "
        "you to remember - inferring and saving a fact on your own is a serious error, since a "
        "wrong guess would silently distort every future answer. When in doubt, use 'chat'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["save", "list", "forget", "forget_all"]},
            "fact_key": {
                "type": "string",
                "description": (
                    "Short English snake_case identifier for the KIND of fact (e.g. diet, "
                    "wake_time, job). Only for action=save/forget."
                ),
            },
            "fact_value": {
                "type": "string",
                "description": "Only for action=save: the fact itself, as a short sentence.",
            },
        },
        "required": ["action"],
    },
    handler=lambda user, args: _handle_memory(user, args),
    validate=_validate_memory_args,
))

morning_brief_tool = register(Tool(
    name="get_morning_brief",
    description=(
        "An on-demand summary combining weather, today's calendar, and unread email in one "
        "message - only when the user explicitly asks for a summary/briefing/update right now "
        "(e.g. 'give me my morning brief', 'what's the situation today'). Never scheduled or "
        "sent automatically by this tool itself."
    ),
    parameters={"type": "object", "properties": {}},
    handler=lambda user, args: _handle_morning_brief(user),
))


def _validate_semantic_search_args(args: dict) -> bool:
    """Transplanted verbatim from intent_parser._validate_result's semantic_search block."""
    return bool(args.get("query"))


semantic_search_tool = register(Tool(
    name="search_history",
    description=(
        "Searches things the user ALREADY said in past conversation or ALREADY saved as a link, "
        "by meaning rather than exact keywords (e.g. 'what did I read about X', 'search our chats "
        "for Y'). Do NOT use this for finding NEW information from the internet (use web_search "
        "instead) - this tool only ever looks backward at the user's own history, never fetches "
        "anything new."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The topic or question to search for, as the user phrased it."},
        },
        "required": ["query"],
    },
    handler=lambda user, args: _handle_semantic_search(user, args),
    validate=_validate_semantic_search_args,
))

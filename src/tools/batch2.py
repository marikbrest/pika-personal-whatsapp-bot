"""
Batch 2 of the function-calling migration: the 4 tools built earliest the
same day as this migration's cutover (2026-09-14) - drive, web_search,
email_analyze, watch_manage. Chosen first for batch 2 because they are the
freshest and most tool-shaped: renders_own_reply=True, no admin_only, no
pending-state complexity, and (unlike the 3 pilot tools) their handlers
already live in webhook_handler.py as thin (user, args) -> str functions
with no old-intent-shaped reply text to strip out.

Every description below carries an explicit "do NOT use this for..."
boundary against the specific intents it could plausibly be confused with -
the lesson from the pilot cutover's own first-day bug (manage_tasks silently
absorbing a reminder-with-a-time because nothing told it not to). Each
boundary is transplanted from the matching "⚠️ הבחנה" note already in
src/intent_parser.py's prompt for these same categories - not invented here,
just ported from what the old classifier already had to learn the hard way.
"""
from src.tools.registry import Tool, register
from src.webhook_handler import _handle_drive, _handle_email_analyze, _handle_watch_manage, _handle_web_search

web_search_tool = register(Tool(
    name="web_search",
    description=(
        "Answers a question that needs current, real, verifiable information from the actual "
        "internet - news, recent events, facts you cannot be fully confident about from training "
        "knowledge, opening hours, 'what happened with X'. Do NOT use this for an ordinary "
        "question you already know confidently (use 'chat'), for weather (use get_weather), or "
        "for a stock/crypto price (use get_market_quote)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A clear, search-ready phrasing of the question - not necessarily verbatim what the user typed.",
            },
        },
        "required": ["query"],
    },
    handler=lambda user, args: _handle_web_search(args),
))


def _validate_drive_args(args: dict) -> bool:
    """Transplanted verbatim from intent_parser._validate_result's drive block."""
    action = args.get("action")
    if action not in ("search", "save_note"):
        return False
    if action == "search" and not args.get("query"):
        return False
    if action == "save_note" and not (args.get("filename") and args.get("content")):
        return False
    return True


drive_tool = register(Tool(
    name="manage_drive",
    description=(
        "Google Drive: search existing files by name or content, or save new text as a new "
        "file. Do NOT use this to save a URL/link the user sent (a separate feature - use "
        "'chat' instead) and do NOT use this for Gmail/email search (a completely different "
        "feature - use 'chat' to let that fall through to the real handler)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["search", "save_note"]},
            "query": {
                "type": "string",
                "description": "Only for action=search: what to search for - a file name or something from its content.",
            },
            "filename": {
                "type": "string",
                "description": "Only for action=save_note: a short filename including a .txt extension.",
            },
            "content": {
                "type": "string",
                "description": "Only for action=save_note: the full text content to save, as the user asked.",
            },
        },
        "required": ["action"],
    },
    handler=lambda user, args: _handle_drive(user, args),
    validate=_validate_drive_args,
))


def _validate_email_analyze_args(args: dict) -> bool:
    """Transplanted verbatim from intent_parser._validate_result's email_analyze block."""
    action = args.get("action")
    if action not in ("summarize", "unanswered"):
        return False
    if action == "summarize" and not args.get("query"):
        return False
    return True


email_analyze_tool = register(Tool(
    name="analyze_email",
    description=(
        "Deep analysis of Gmail: summarize one specific email thread and extract any "
        "commitments/deadlines mentioned in it, or list emails the user sent that are still "
        "waiting for a reply. Do NOT use this just to show a list of recent emails or their "
        "headers/snippets (a different, simpler feature) - use 'chat' instead for that."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["summarize", "unanswered"]},
            "query": {
                "type": "string",
                "description": (
                    "Only for action=summarize: a Gmail search identifying the thread - a name, "
                    "subject, or keyword, e.g. 'dani project'."
                ),
            },
        },
        "required": ["action"],
    },
    handler=lambda user, args: _handle_email_analyze(user, args),
    validate=_validate_email_analyze_args,
))


def _validate_watch_manage_args(args: dict) -> bool:
    """Transplanted verbatim from intent_parser._validate_result's watch_manage block."""
    action = args.get("action")
    if action not in ("add", "list", "cancel"):
        return False
    if action == "add":
        watch_type = args.get("watch_type")
        if watch_type not in ("email_reply", "web_page"):
            return False
        if watch_type == "email_reply" and not args.get("query"):
            return False
        if watch_type == "web_page" and not args.get("url"):
            return False
    if action == "cancel" and not args.get("match"):
        return False
    return True


watch_manage_tool = register(Tool(
    name="manage_watches",
    description=(
        "Registers, lists, or cancels a watch that notifies the user when something changes: "
        "either a reply arriving on a specific email thread, or a web page's content changing. "
        "Do NOT use this for package/shipment tracking (a separate, already-existing feature) - "
        "use 'chat' instead so that falls through to the real handler."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "list", "cancel"]},
            "watch_type": {
                "type": "string",
                "enum": ["email_reply", "web_page"],
                "description": "Only for action=add: 'email_reply' for waiting on a reply, 'web_page' for a URL.",
            },
            "query": {
                "type": "string",
                "description": "Only for action=add with watch_type=email_reply: a Gmail search identifying the thread.",
            },
            "url": {
                "type": "string",
                "description": "Only for action=add with watch_type=web_page: the URL to watch.",
            },
            "match": {
                "type": "string",
                "description": "Only for action=cancel: an identifying description of which watch to stop.",
            },
        },
        "required": ["action"],
    },
    handler=lambda user, args: _handle_watch_manage(user, args),
    validate=_validate_watch_manage_args,
))

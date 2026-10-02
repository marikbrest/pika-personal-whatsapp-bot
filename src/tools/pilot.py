"""
The three pilot tools for stage A of the function-calling migration: weather,
market, task_manage. Chosen because all three are renders_own_reply=True (no
reply_text schema plumbing to get right on the first try), none needs
admin_only, and none has pending-state complexity (unlike email_draft) - the
smallest real slice that still exercises the whole pipeline end to end.

Handlers are thin wrappers around the EXISTING, already-tested
webhook_handler functions - not reimplementations. Two copies of "how to
format a weather reply" is exactly the kind of drift this migration must not
introduce.
"""
from src.tools.registry import Tool, register
from src.webhook_handler import _handle_market, _handle_task_manage, _handle_weather

weather_tool = register(Tool(
    name="get_weather",
    description=(
        "Current weather or forecast for a city. Use for any question about weather, "
        "temperature, rain, or forecast."
    ),
    parameters={
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": (
                    "City name mentioned, in English (e.g. 'Tel Aviv' not 'תל אביב') so the "
                    "weather API resolves it correctly. Omit entirely if no city was mentioned."
                ),
            },
            "day_offset": {
                "type": "integer",
                "description": "Days ahead from today: 0=now/today, 1=tomorrow, 2=the day after, etc.",
            },
            "is_range": {
                "type": "boolean",
                "description": (
                    "true if a range of days was requested ('this week', 'the next 3 days'), "
                    "false for a single day or right now."
                ),
            },
            "range_days": {
                "type": "integer",
                "description": "Only when is_range is true: how many days to show starting at day_offset, max 7.",
            },
        },
        "required": ["day_offset", "is_range"],
    },
    handler=lambda user, args: _handle_weather(args),
))

market_tool = register(Tool(
    name="get_market_quote",
    description="Current price of a stock or cryptocurrency.",
    parameters={
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": (
                    "Yahoo Finance symbol: a plain stock like 'AAPL'/'GOOGL'/'TSLA', an Israeli "
                    "stock with a '.TA' suffix like 'TEVA.TA', or crypto with a '-USD' suffix "
                    "like 'BTC-USD'/'ETH-USD'."
                ),
            },
        },
        "required": ["symbol"],
    },
    handler=lambda user, args: _handle_market(args),
))


def _validate_task_manage_args(args: dict) -> bool:
    """Transplanted verbatim from intent_parser._validate_result's task_manage
    block - the exact same conditional-required rules, just expressed as a
    tool-level guard instead of one branch in a 300-line function."""
    action = args.get("action")
    if action not in ("add", "list", "done", "delete", "clear"):
        return False
    if action == "add" and not args.get("content"):
        return False
    if action in ("done", "delete") and not args.get("match"):
        return False
    return True


task_manage_tool = register(Tool(
    name="manage_tasks",
    description=(
        "Shopping or to-do lists: add an item, list items, mark one done, delete one, or clear "
        "a list. Do NOT use this if the message gives a specific time or date to be reminded at "
        "(e.g. 'remind me tomorrow at 10 to buy milk') - that is a scheduled reminder, a "
        "different feature this tool cannot handle; pick 'chat' instead for those, never guess "
        "that they mean the list."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "list", "done", "delete", "clear"]},
            "content": {
                "type": "string",
                "description": "Only for action=add: the item itself, without leading phrasing like 'add to the list'.",
            },
            "match": {
                "type": "string",
                "description": (
                    "Only for action=done/delete: an identifying description of which item, by "
                    "its content - never a position number, since list order shifts as items "
                    "are added or completed."
                ),
            },
            "list_name": {
                "type": "string",
                "description": (
                    "The list name only if the user named one explicitly ('קניות', 'מטלות', "
                    "'עבודה'). Omit entirely to use the default list."
                ),
            },
        },
        "required": ["action"],
    },
    handler=lambda user, args: _handle_task_manage(user, args),
    validate=_validate_task_manage_args,
))

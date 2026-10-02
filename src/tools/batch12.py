"""
New feature (2026-09-15): manage_kids_schedule. the admin wants to save each of
his kids' weekly school/activity timetable and have the bot proactively
remind him every evening what tomorrow looks like, instead of only reacting
to on-demand requests the way every other tool so far does. The proactive
half (scheduler.check_and_send_kids_schedule_reminders) is separate
infrastructure that needs no wiring here - this tool is only the chat-driven
CRUD side: set/set_week/list/delete for a kid's timetable. day_of_week
reuses the same mon/tue/.../sun convention create_reminder's schedule_days
already uses, rather than inventing a new day format.

set_week (2026-09-15 same-day addition): lets a photo of a printed/
handwritten weekly schedule board save every visible day in one call,
instead of the user typing each day separately. The image never reaches
this tool directly - it goes through the existing two-step media pipeline
(_transcribe_media describes/transcribes it to text first, see that
function's own prompt for the schedule-board-specific instructions added
alongside its existing receipt/document handling), and THIS tool's schema
is what the resulting text gets classified against, exactly like any other
media message.

"list" also backs a kid asking about their OWN schedule (see
webhook_handler._handle_kids_schedule's phone-number fallback) - a
registered kid-user with no schedule of their own under their own account
resolves to whatever their parent saved for them, found by matching the
kid's own WhatsApp number against a contact a parent saved.
"""
from src.scheduler import _WEEKDAY_NAMES
from src.tools.registry import Tool, register
from src.webhook_handler import _handle_kids_schedule


def _validate_kids_schedule_args(args: dict) -> bool:
    action = args.get("action")
    if action not in ("set", "set_week", "list", "delete"):
        return False
    if action == "set":
        if not (args.get("kid_name") and args.get("content")):
            return False
        if args.get("day_of_week") not in _WEEKDAY_NAMES:
            return False
    if action == "set_week":
        if not args.get("kid_name"):
            return False
        days = args.get("days")
        if not days or not isinstance(days, list):
            return False
        for entry in days:
            if not isinstance(entry, dict):
                return False
            if entry.get("day_of_week") not in _WEEKDAY_NAMES or not entry.get("content"):
                return False
    if action == "delete":
        if not args.get("kid_name"):
            return False
        if args.get("day_of_week") and args["day_of_week"] not in _WEEKDAY_NAMES:
            return False
    return True


manage_kids_schedule_tool = register(Tool(
    name="manage_kids_schedule",
    description=(
        "Saves, views, or deletes a kid's RECURRING WEEKLY school/activity timetable, so the bot "
        "can proactively remind the user every evening what tomorrow looks like, and so the kid "
        "themselves can ask what's on it. Use action=set for ONE day at a time (e.g. 'תוסיף למערכת "
        "של דני ביום שני חשבון בשמונה'). Use action=set_week when MULTIPLE days are being given at "
        "once - most commonly right after a photo of a printed/handwritten weekly schedule board "
        "was described (the description will name each day and its content) - save every day found "
        "in ONE call rather than asking the user to repeat themselves per day. Use action=list for "
        "'מה יש במערכת של דני' or a kid asking about their own schedule ('מה יש לי מחר', 'מה "
        "המערכת שלי'). Do NOT use create_reminder/manage_reminders for this - those are one-off or "
        "simple recurring alerts with ONE piece of content, not a per-weekday timetable with "
        "different content each day, and they do not drive the nightly 'tomorrow's schedule' "
        "message. Do NOT use manage_tasks for this either - that's a flat shopping/to-do list, not "
        "a weekly-recurring, per-day, per-kid timetable."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["set", "set_week", "list", "delete"]},
            "kid_name": {
                "type": "string",
                "description": (
                    "The kid's name. Required for set/set_week/delete. Omit for list to show every "
                    "kid's saved schedule (or, for a kid asking about themselves, omit it too - the "
                    "code resolves who they are from their own WhatsApp number)."
                ),
            },
            "day_of_week": {
                "type": "string",
                "enum": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                "description": (
                    "Required for action=set only. For delete, omit to delete the kid's ENTIRE "
                    "saved schedule; give a day to delete just that day. Not used for list/set_week."
                ),
            },
            "content": {
                "type": "string",
                "description": (
                    "Required for action=set: that day's classes/activities as the user described "
                    "them (subjects, times, anything relevant), in the user's own words/language."
                ),
            },
            "days": {
                "type": "array",
                "description": (
                    "Required for action=set_week: one entry per day found, each with its own "
                    "day_of_week and content (same rules as the single-day 'content' field above). "
                    "Include every day the description actually named - do not invent days that "
                    "weren't there."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "day_of_week": {"type": "string", "enum": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]},
                        "content": {"type": "string"},
                    },
                },
            },
        },
        "required": ["action"],
    },
    handler=lambda user, args: _handle_kids_schedule(user, args),
    validate=_validate_kids_schedule_args,
))

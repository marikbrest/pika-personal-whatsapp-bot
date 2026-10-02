"""
New feature (2026-09-17): manage_persistent_reminders. the admin wants
reminders to his kids "בדרך שהם לא יפספסו וזה לא יפסיק עד שהם יאשרו שהם
עשו" (a way they won't miss, that doesn't stop until they confirm they did
it) - a reminder that keeps re-nagging its recipient every
retry_interval_minutes (5, per the admin's own answer) until they explicitly
confirm, instead of firing once like an ordinary reminder. Confirmed:
either parent can create one, each for the kids saved as their own
contacts.

This tool is only the chat-driven create/list/cancel side; the actual
repeated delivery + escalation-to-parent-on-no-response is
scheduler.check_and_send_persistent_reminders, and the recipient's own
confirmation is handled as an early, special-cased text check in
webhook_handler._process_single_message (see _check_task_confirmation) -
NOT a registered tool, since it only makes sense conditionally (only when
the SENDER of the incoming message is the target of a pending nag), the
same "physical exclusion is real defence" principle other conditional tools
in this codebase already use, just implemented as a pre-classification
special case instead of a tools_for() exclusion - simpler here since
recognizing "did the kid just confirm this specific task" is a one-off
targeted question, not a general-purpose tool Gemini should ever be
choosing between.

Same-day follow-up (2026-09-17): create can now also be RECURRING
(schedule_type/schedule_time/schedule_days, same convention as
create_reminder - "כל יום בערב", "כל יום א,ב,ג") instead of only a single
one-off nag-until-confirmed cycle, and list can be scoped to one kid.
"""
from src.tools.registry import Tool, register
from src.webhook_handler import _handle_persistent_reminders


def _validate_persistent_reminders_args(args: dict) -> bool:
    action = args.get("action")
    if action not in ("create", "list", "cancel"):
        return False
    if action == "create":
        has_recipient = bool(args.get("recipient_names")) or bool(args.get("recipient_name"))
        if not (has_recipient and args.get("content")):
            return False
        schedule_type = args.get("schedule_type") or "once"
        if schedule_type not in ("once", "daily", "weekly"):
            return False
        if schedule_type in ("daily", "weekly") and not args.get("schedule_time"):
            return False
        if schedule_type == "weekly" and not args.get("schedule_days"):
            return False
    if action == "cancel" and not args.get("match"):
        return False
    return True


manage_persistent_reminders_tool = register(Tool(
    name="manage_persistent_reminders",
    description=(
        "Creates, lists, or cancels a PERSISTENT nagging reminder for a saved contact (typically a "
        "kid) - one that keeps repeating every few minutes until the recipient explicitly confirms "
        "they did it, and notifies the sender if they never do. Can be one-off OR recurring "
        "(daily/weekly) - each occurrence runs its own full nag-until-confirmed cycle. Use this "
        "ONLY when the user explicitly wants something that repeats/nags/insists until confirmed "
        "(e.g. 'תזכיר לדני כל כמה דקות לעשות שיעורי בית עד שהוא יגיד שעשה', 'תזכיר לו כל ערב "
        "להאכיל את הכלב עד שהוא יאשר', 'כל יום ראשון שני ושלישי תזכיר לו לקחת תרופה עד שיגיד "
        "שלקח'). Do NOT use this for an ordinary reminder that fires once with no repeat-until-"
        "confirmed behavior (use create_reminder for that, even if it's daily/weekly) - this tool "
        "is specifically for the repeat-every-few-minutes-until-confirmed mechanism, not a general "
        "reminder scheduler."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "list", "cancel"]},
            "recipient_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Only for action=create: the saved contacts' names (each must already be saved), "
                    "required - include EVERY name mentioned (e.g. 'תזכיר לדני, נועה ותומר' -> all 3 "
                    "names, not just the first). One independent nag-until-confirmed reminder is "
                    "created per name, so each kid confirms their own separately."
                ),
            },
            "recipient_name": {
                "type": "string",
                "description": (
                    "Only for action=list: optionally scope the list to just this one recipient - "
                    "omit to show every recipient's active reminders. Not used for action=create - "
                    "use recipient_names for that."
                ),
            },
            "content": {
                "type": "string",
                "description": "Only for action=create: what the recipient needs to do.",
            },
            "schedule_type": {
                "type": "string",
                "enum": ["once", "daily", "weekly"],
                "description": (
                    "Only for action=create. 'once' (default, omit if not mentioned): a single "
                    "nag-until-confirmed cycle. 'daily'/'weekly': the whole cycle repeats every day/"
                    "on the given weekdays, resetting fresh each time regardless of whether the "
                    "previous occurrence was ever confirmed."
                ),
            },
            "schedule_time": {
                "type": "string",
                "description": (
                    "Only for action=create. For schedule_type=once, only if a specific future start "
                    "time was given: full ISO 'YYYY-MM-DDTHH:MM:SS', no timezone - omit to start "
                    "nagging right away. For daily/weekly (required): 'HH:MM', the time each "
                    "occurrence's nagging should start."
                ),
            },
            "schedule_days": {
                "type": "string",
                "description": (
                    "Only for action=create with schedule_type=weekly (required then): comma-"
                    "separated days from mon,tue,wed,thu,fri,sat,sun. Omit entirely otherwise."
                ),
            },
            "match": {
                "type": "string",
                "description": "Only for action=cancel: an identifying description of which one, by content or recipient name.",
            },
        },
        "required": ["action"],
    },
    handler=lambda user, args: _handle_persistent_reminders(user, args),
    validate=_validate_persistent_reminders_args,
))

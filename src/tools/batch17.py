"""
Context-Aware Gatekeeper, stage 1 (2026-09-27, planning discussed the same
day) - the delivery-policy management tools. manage_proactive_settings is
the opt-in switch plus quiet hours/daily cap/explicit "busy" override;
manage_vip_senders is the bypass list. Neither tool sends anything
proactively itself - they only ever write settings src.proactive.
should_deliver_now reads later, when a real collector (currently just
scheduler.check_and_monitor_calendar_changes) has something to report.
"""
from src.tools.registry import Tool, register
from src.webhook_handler import _handle_manage_proactive_settings, _handle_manage_vip_senders

_PROACTIVE_ACTIONS = (
    "enable", "disable", "status", "set_status", "clear_status", "set_quiet_hours", "set_daily_cap",
    "set_meeting_lead_time",
)


def _validate_manage_proactive_settings_args(args: dict) -> bool:
    action = args.get("action")
    if action not in _PROACTIVE_ACTIONS:
        return False
    if action == "set_status" and not args.get("minutes"):
        return False
    if action == "set_quiet_hours" and not (args.get("start") and args.get("end")):
        return False
    if action == "set_daily_cap" and not args.get("cap"):
        return False
    if action == "set_meeting_lead_time" and not args.get("lead_minutes"):
        return False
    return True


manage_proactive_settings_tool = register(Tool(
    name="manage_proactive_settings",
    description=(
        "Manages the bot's PROACTIVE/unattended notifications (currently: calendar-change alerts) - "
        "turning them on/off, checking status, setting quiet hours, the daily notification cap, or a "
        "temporary 'I'm busy' window that also pauses proactive messages, or the lead time for meeting "
        "pre-briefs. Use for requests like 'תפעיל את המצב היזום', 'אני בפגישה עד 16:00', 'שקט עד מחר', "
        "'תשנה את שעות השקט ל-23:00 עד 06:00', 'מקסימום 4 התראות ביום', 'תזכיר לי 10 דקות לפני פגישה'. "
        "Do NOT use this for an ordinary one-off reminder (use create_reminder) - this only ever manages "
        "the standing proactive-notification behavior itself."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(_PROACTIVE_ACTIONS)},
            "minutes": {
                "type": "integer",
                "description": (
                    "Only for action=set_status: how many minutes from now to stay quiet - convert a "
                    "phrase like 'עד 16:00' or 'שקט עד מחר' into minutes from the current time (given in "
                    "context above)."
                ),
            },
            "start": {"type": "string", "description": "Only for action=set_quiet_hours: start time, 24h 'HH:MM'."},
            "end": {"type": "string", "description": "Only for action=set_quiet_hours: end time, 24h 'HH:MM'."},
            "cap": {"type": "integer", "description": "Only for action=set_daily_cap: max proactive notifications per day."},
            "lead_minutes": {
                "type": "integer",
                "description": "Only for action=set_meeting_lead_time: how many minutes before a meeting to send its pre-brief.",
            },
        },
        "required": ["action"],
    },
    handler=lambda user, args: _handle_manage_proactive_settings(user, args),
    validate=_validate_manage_proactive_settings_args,
))


def _validate_manage_vip_senders_args(args: dict) -> bool:
    action = args.get("action")
    if action not in ("add", "list", "remove"):
        return False
    if action in ("add", "remove") and not args.get("identifier"):
        return False
    return True


manage_vip_senders_tool = register(Tool(
    name="manage_vip_senders",
    description=(
        "Manages the user's own VIP list (email addresses or WhatsApp numbers) that bypass quiet hours "
        "and a temporary 'busy' status for proactive notifications - never the daily cap. Use for "
        "requests like 'תוסיף את דני ל-VIP', 'תוסיף את הגן של הילד כ-VIP', 'מי ברשימת ה-VIP שלי', "
        "'תסיר את X מה-VIP'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "list", "remove"]},
            "identifier": {
                "type": "string",
                "description": (
                    "Only for action=add/remove: the email address or WhatsApp number. If the user "
                    "only gave a name (e.g. 'תוסיף את דני ל-VIP'), pass the name as-is here - the "
                    "handler resolves it against the user's own saved contacts."
                ),
            },
            "label": {
                "type": "string",
                "description": "Only for action=add, optional: a short human label (e.g. 'הגן של הילד').",
            },
        },
        "required": ["action"],
    },
    handler=lambda user, args: _handle_manage_vip_senders(user, args),
    validate=_validate_manage_vip_senders_args,
))

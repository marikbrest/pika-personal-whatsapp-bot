"""
New feature (2026-09-15): manage_daily_meetings_summary. the admin asked for an
option so that "אני רוצה סיכום פגישות שלי כל בוקר" (I want a summary of my
meetings every morning) actually works - a proactive, opt-in daily calendar
summary, distinct from get_morning_brief (batch3.py), which is deliberately
on-demand-only by design (see morning_brief.py's own docstring). This tool
only flips the opt-in flag and its send time (enable/disable/status); the
actual daily send is scheduler.check_and_send_daily_meetings_summaries,
which reuses morning_brief._calendar_section rather than duplicating the
fetch/format.

2026-09-17: the send time became per-user configurable (was a single fixed
07:00 for everyone) - the admin asked for this after a real user (the
other parent) wanted 9:00 and the bot could only offer 7:00.
"""
import re

from src.tools.registry import Tool, register
from src.webhook_handler import _handle_daily_meetings_summary

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _validate_daily_meetings_summary_args(args: dict) -> bool:
    if args.get("action") not in ("enable", "disable", "status"):
        return False
    if args.get("action") == "enable" and args.get("time") is not None:
        if not _TIME_RE.match(args["time"]):
            return False
    return True


manage_daily_meetings_summary_tool = register(Tool(
    name="manage_daily_meetings_summary",
    description=(
        "Turns an AUTOMATIC daily calendar summary on/off, changes what time it arrives, or reports "
        "whether it's currently on - sent proactively every morning without being asked, e.g. 'אני "
        "רוצה סיכום פגישות שלי כל בוקר', 'תשלח לי את זה ב-9 בבוקר', 'תעביר את הסיכום היומי ל-8:30', "
        "'תפסיק לשלוח לי כל בוקר את היומן', 'האם הסיכום היומי פעיל אצלי'. The send time is fully "
        "configurable per user (use action=enable with a time to set/change it - re-enabling with a "
        "new time updates an already-active summary's time too, no need to disable first). Do NOT "
        "use this for a one-time, right-now summary (use get_morning_brief for that - it never "
        "enables or changes any ongoing setting) and do NOT use this to view/create/update a "
        "specific calendar event (use manage_calendar for that) - this tool only ever manages the "
        "standing daily-send preference itself."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["enable", "disable", "status"]},
            "time": {
                "type": "string",
                "description": (
                    "Only for action=enable: the desired send time in 24h 'HH:MM' format (e.g. "
                    "'09:00'), only if the user specified one (directly, or by changing an existing "
                    "time). Omit to keep the current/default time unchanged."
                ),
            },
        },
        "required": ["action"],
    },
    handler=lambda user, args: _handle_daily_meetings_summary(user, args),
    validate=_validate_daily_meetings_summary_args,
))

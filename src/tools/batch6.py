"""
Batch 6 of the function-calling migration (2026-09-14): draft_email,
respond_to_email_draft, manage_reminders, track_package - the last of
the admin's original 5-priority list's supporting intents, and the batch flagged
from the start as genuinely harder than the previous ones, for one real
reason: email_action absolutely depends on knowing whether a draft is
currently pending (the old classifier gets this from intent_parser's
_format_pending_draft context block; the new pipeline's classify_with_tools
call carried NO context at all through batches 1-5, since none of those 17
tools needed any). That limitation could not just be carried forward here -
see _classify_text_with_cutover in webhook_handler.py, which now:
  1) excludes respond_to_email_draft from the candidate tool set entirely
     when there is no pending draft (the same "physical exclusion is real
     defence" principle admin_only already uses in registry.tools_for(),
     just keyed on draft state instead of user identity), and
  2) prepends a small pending-draft context block to the Gemini call's
     `contents` when one exists, so Gemini can actually recognise an
     approval/cancellation/edit reply as such.
manage_reminders and track_package need no such context - match_content
resolution (reminder) and package lookup are both done in code from the DB,
exactly as they already were under the old classifier.

Handlers are thin, non-reimplemented wrappers over the existing, tested
_handle_email_draft/_handle_email_action/_handle_reminder_manage/
_handle_package_status - the one new piece of logic is
_handle_email_action_tool in webhook_handler.py, which fetches the pending
draft itself (a Tool handler only ever gets (user, args), unlike the old
classifier's dispatch which already had pending_draft threaded through from
_process_single_message).
"""
from src.intent_parser import FALLBACK_REPLY
from src.tools.registry import Tool, register
from src.webhook_handler import (
    _handle_email_action_tool,
    _handle_email_draft,
    _handle_package_status,
    _handle_reminder_manage,
)


def _validate_draft_email_args(args: dict) -> bool:
    """Transplanted verbatim from intent_parser._validate_result's email_draft block."""
    return {"to", "subject", "body"}.issubset(args.keys())


draft_email_tool = register(Tool(
    name="draft_email",
    description=(
        "Composes a new email as a DRAFT for the user's approval - it does NOT send it. Use this "
        "for any request to write/compose/send an email, in any language ('write an email to X "
        "saying...', 'תכתוב לדני שאני מאחר'), as long as there is no draft already pending "
        "approval right now. Do NOT use this if a draft is currently pending (use "
        "respond_to_email_draft instead for any reply to it - approval, cancellation, or a "
        "change request)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "to": {
                "type": "string",
                "description": (
                    "Recipient email address. If the user only gave a name and you don't have "
                    "their email address, use the literal string 'UNKNOWN' and explain in "
                    "reply_text that an email address is needed."
                ),
            },
            "subject": {"type": "string", "description": "Short, focused subject line."},
            "body": {
                "type": "string",
                "description": "Full, polite email body, in the same language the user wrote in.",
            },
            "reply_text": {
                "type": "string",
                "description": (
                    "Unlike most tools, this one IS the final message shown to the user - present "
                    "the draft clearly, e.g. 'הנה טיוטה:\\nאל: ...\\nנושא: ...\\n\\n[body]\\n\\n"
                    "לשלוח? תגיד כן/לא/תשנה משהו', in the same language as the user's message."
                ),
            },
        },
        "required": ["to", "subject", "body", "reply_text"],
    },
    handler=lambda user, args: _handle_email_draft(user, args, args.get("reply_text") or FALLBACK_REPLY),
    validate=_validate_draft_email_args,
))


def _validate_respond_to_email_draft_args(args: dict) -> bool:
    """Transplanted verbatim from intent_parser._validate_result's email_action block."""
    return args.get("action") in ("send", "cancel", "edit")


respond_to_email_draft_tool = register(Tool(
    name="respond_to_email_draft",
    description=(
        "Handles the user's reply to an email draft that is CURRENTLY PENDING their approval - "
        "approving it (action=send), cancelling it (action=cancel), or asking for a change "
        "(action=edit). Only usable when a draft is actually pending right now (the code excludes "
        "this tool otherwise, so if you are being offered it, one is pending). Do NOT use this to "
        "start composing a brand-new email (use draft_email for that)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["send", "cancel", "edit"]},
            "edit_instructions": {
                "type": "string",
                "description": "Only for action=edit: briefly, what to change about the draft. Omit for send/cancel.",
            },
        },
        "required": ["action"],
    },
    handler=lambda user, args: _handle_email_action_tool(user, args),
    validate=_validate_respond_to_email_draft_args,
))


def _validate_manage_reminders_args(args: dict) -> bool:
    """Transplanted verbatim from intent_parser._validate_result's reminder_manage block."""
    action = args.get("action")
    if action not in ("list", "cancel", "reschedule", "snooze", "edit_content"):
        return False
    if action == "reschedule" and not (args.get("schedule_type") and args.get("schedule_time")):
        return False
    if action == "snooze" and not args.get("snooze_minutes"):
        return False
    if action == "edit_content" and not args.get("new_content"):
        return False
    return True


manage_reminders_tool = register(Tool(
    name="manage_reminders",
    description=(
        "Views, cancels, reschedules, snoozes, or edits the wording of an EXISTING reminder that "
        "was already created (e.g. 'what do I have scheduled', 'cancel the doctor reminder', 'move "
        "the doctor reminder to 10', 'snooze it an hour', 'change the reminder to say...'). Do NOT "
        "use this to create a brand-new reminder (use create_reminder for that) - this tool only "
        "ever acts on a reminder that already exists."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "cancel", "reschedule", "snooze", "edit_content"]},
            "match_content": {
                "type": "string",
                "description": (
                    "For any action except list: a description of which reminder is meant, by its "
                    "content/topic, not a serial number. For snooze this may be omitted if the user "
                    "did not say which one - the code falls back to the most recently fired reminder."
                ),
            },
            "schedule_type": {
                "type": "string", "enum": ["once", "daily", "weekly"],
                "description": "Only for action=reschedule.",
            },
            "schedule_time": {
                "type": "string",
                "description": (
                    "Only for action=reschedule. For once: full future ISO datetime "
                    "'YYYY-MM-DDTHH:MM:SS', no timezone. For daily/weekly: 'HH:MM'."
                ),
            },
            "schedule_days": {
                "type": "string",
                "description": (
                    "Only for a weekly action=reschedule: comma-separated days from "
                    "mon,tue,wed,thu,fri,sat,sun. Omit otherwise."
                ),
            },
            "snooze_minutes": {
                "type": "integer",
                "description": "Only for action=snooze: how many minutes to push it back by.",
            },
            "new_content": {
                "type": "string",
                "description": "Only for action=edit_content: the new wording of the reminder.",
            },
        },
        "required": ["action"],
    },
    handler=lambda user, args: _handle_reminder_manage(user, args),
    validate=_validate_manage_reminders_args,
))

track_package_tool = register(Tool(
    name="track_package",
    description=(
        "Reports the live status of the user's tracked packages/orders, or starts tracking a new "
        "one if the user directly gives a tracking number. Use for 'where's my package', 'what's "
        "the status of my order', or a pasted tracking number. Do NOT use this for general email "
        "questions or summaries (use read_emails or analyze_email instead) - this is specifically "
        "about shipment tracking status."
    ),
    parameters={
        "type": "object",
        "properties": {
            "tracking_number": {
                "type": "string",
                "description": (
                    "If the user directly gave a tracking number in the message, the exact number "
                    "with no extra whitespace. Omit entirely otherwise - most requests are just "
                    "'where's my package', with no number given."
                ),
            },
            "description": {
                "type": "string",
                "description": (
                    "Only alongside tracking_number: a short description of what was shipped, if "
                    "known from context (e.g. 'shoes from Amazon'). Omit otherwise."
                ),
            },
        },
        "required": [],
    },
    handler=lambda user, args: _handle_package_status(user, args),
))

"""
src.tools.batch6 - the sixth and final batch of the function-calling
migration (draft_email, respond_to_email_draft, manage_reminders,
track_package). The first batch where a tool (respond_to_email_draft)
genuinely needs context beyond the message text itself - see
test_tool_cutover.py for the pending-draft-context tests, which cover
webhook_handler.py's side of that (this file covers the tools themselves:
wiring, validation, and handler dispatch, the same scope every previous
batch's own test file covers).
"""
from src.tools.batch6 import (
    _validate_draft_email_args,
    _validate_manage_reminders_args,
    _validate_respond_to_email_draft_args,
    draft_email_tool,
    manage_reminders_tool,
    respond_to_email_draft_tool,
    track_package_tool,
)
from src.tools.dispatch import execute_tool
from src.tools.registry import tools_for


def test_all_four_batch6_tools_are_registered_and_not_admin_only():
    for tool in (draft_email_tool, respond_to_email_draft_tool, manage_reminders_tool, track_package_tool):
        assert tool in tools_for({"is_admin": False})


def test_draft_email_tool_dispatches_to_the_real_handler(monkeypatch):
    import src.tools.batch6 as batch6

    monkeypatch.setattr(batch6, "_handle_email_draft", lambda user, args, reply: f"draft handled, reply={reply}")
    reply = execute_tool(
        draft_email_tool, {"id": 1},
        {"to": "x@example.com", "subject": "s", "body": "b", "reply_text": "הנה טיוטה"},
    )
    assert reply == "draft handled, reply=הנה טיוטה"


def test_respond_to_email_draft_tool_dispatches_to_the_real_handler(monkeypatch):
    import src.tools.batch6 as batch6

    monkeypatch.setattr(batch6, "_handle_email_action_tool", lambda user, args: f"action: {args['action']}")
    reply = execute_tool(respond_to_email_draft_tool, {"id": 1}, {"action": "send"})
    assert reply == "action: send"


def test_manage_reminders_tool_dispatches_to_the_real_handler(monkeypatch):
    import src.tools.batch6 as batch6

    monkeypatch.setattr(batch6, "_handle_reminder_manage", lambda user, args: f"reminders: {args['action']}")
    reply = execute_tool(manage_reminders_tool, {"id": 1}, {"action": "list"})
    assert reply == "reminders: list"


def test_track_package_tool_dispatches_to_the_real_handler(monkeypatch):
    import src.tools.batch6 as batch6

    monkeypatch.setattr(
        batch6, "_handle_package_status", lambda user, args: f"packages for tracking={args.get('tracking_number')}"
    )
    reply = execute_tool(track_package_tool, {"id": 1}, {"tracking_number": "1Z999"})
    assert reply == "packages for tracking=1Z999"


def test_validate_draft_email_args_requires_to_subject_body():
    assert _validate_draft_email_args({"to": "x@example.com", "subject": "s", "body": "b"}) is True
    assert _validate_draft_email_args({"to": "x@example.com", "subject": "s"}) is False
    assert _validate_draft_email_args({}) is False


def test_validate_respond_to_email_draft_args_requires_a_known_action():
    assert _validate_respond_to_email_draft_args({"action": "send"}) is True
    assert _validate_respond_to_email_draft_args({"action": "cancel"}) is True
    assert _validate_respond_to_email_draft_args({"action": "edit"}) is True
    assert _validate_respond_to_email_draft_args({"action": "bogus"}) is False
    assert _validate_respond_to_email_draft_args({}) is False


def test_validate_manage_reminders_args_matches_intent_parser_rules():
    assert _validate_manage_reminders_args({"action": "list"}) is True
    assert _validate_manage_reminders_args({"action": "cancel", "match_content": "doctor"}) is True
    assert _validate_manage_reminders_args(
        {"action": "reschedule", "schedule_type": "once", "schedule_time": "2026-09-20T10:00:00"}
    ) is True
    assert _validate_manage_reminders_args({"action": "reschedule"}) is False
    assert _validate_manage_reminders_args({"action": "snooze", "snooze_minutes": 15}) is True
    assert _validate_manage_reminders_args({"action": "snooze"}) is False
    assert _validate_manage_reminders_args({"action": "edit_content", "new_content": "buy milk"}) is True
    assert _validate_manage_reminders_args({"action": "edit_content"}) is False
    assert _validate_manage_reminders_args({"action": "bogus"}) is False


def test_draft_email_description_distinguishes_from_respond_to_email_draft():
    assert "respond_to_email_draft" in draft_email_tool.description


def test_respond_to_email_draft_description_distinguishes_from_draft_email():
    assert "draft_email" in respond_to_email_draft_tool.description


def test_manage_reminders_description_distinguishes_from_create_reminder():
    assert "create_reminder" in manage_reminders_tool.description


def test_track_package_description_distinguishes_from_email_tools():
    assert "read_emails" in track_package_tool.description or "analyze_email" in track_package_tool.description

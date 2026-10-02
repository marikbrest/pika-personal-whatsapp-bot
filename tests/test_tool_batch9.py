"""
src.tools.batch9 - confirm_suggestion, the confirm/dismiss side of the
"proactive suggestion from a forwarded message" feature (2026-09-14). See
tests/test_forwarded_suggestions.py for the proposal side
(_suggest_action_from_forwarded) and _handle_confirm_suggestion_tool's own
logic; this file covers just the tool's own wiring, matching the scope every
earlier batch's own test file covers.
"""
from src.tools.batch9 import _validate_confirm_suggestion_args, confirm_suggestion_tool
from src.tools.dispatch import execute_tool
from src.tools.registry import tools_for


def test_confirm_suggestion_tool_is_registered_and_not_admin_only():
    assert confirm_suggestion_tool in tools_for({"is_admin": False})


def test_confirm_suggestion_tool_dispatches_to_the_real_handler(monkeypatch):
    import src.tools.batch9 as batch9

    monkeypatch.setattr(batch9, "_handle_confirm_suggestion_tool", lambda user, args: f"handled: {args['action']}")
    reply = execute_tool(confirm_suggestion_tool, {"id": 1}, {"action": "confirm"})
    assert reply == "handled: confirm"


def test_validate_confirm_suggestion_args():
    assert _validate_confirm_suggestion_args({"action": "confirm"}) is True
    assert _validate_confirm_suggestion_args({"action": "dismiss"}) is True
    assert _validate_confirm_suggestion_args({"action": "bogus"}) is False
    assert _validate_confirm_suggestion_args({}) is False

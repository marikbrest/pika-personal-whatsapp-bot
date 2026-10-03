"""
src.tools.batch10 - explain_capabilities ("what can you do" / "help"),
grounded in the real tool registry rather than a free-form Gemini answer.
See webhook_handler._handle_explain_capabilities and _CAPABILITY_GROUPS for
the full reasoning.
"""
from src.tools.batch10 import explain_capabilities_tool
from src.tools.dispatch import execute_tool
from src.tools.registry import tools_for
from src.webhook_handler import _CAPABILITY_GROUPS, _handle_explain_capabilities


def test_explain_capabilities_tool_is_registered_and_not_admin_only():
    assert explain_capabilities_tool in tools_for({"is_admin": False})


def test_explain_capabilities_tool_dispatches_to_the_real_handler(monkeypatch):
    import src.tools.batch10 as batch10

    monkeypatch.setattr(batch10, "_handle_explain_capabilities", lambda user: "handled")
    reply = execute_tool(explain_capabilities_tool, {"id": 1, "is_admin": False}, {})
    assert reply == "handled"


def test_handle_explain_capabilities_omits_admin_only_lines_for_a_regular_user():
    reply = _handle_explain_capabilities({"id": 1, "is_admin": False})
    assert "מנהל בלבד" not in reply
    assert "תזכורת" in reply  # a real, non-admin capability is still present


def test_handle_explain_capabilities_includes_admin_only_lines_for_an_admin():
    reply = _handle_explain_capabilities({"id": 1, "is_admin": True})
    assert "מנהל בלבד" in reply


def test_handle_explain_capabilities_never_lists_a_tool_that_is_not_actually_registered():
    """The whole point of grounding this in tools_for() instead of a static
    string: every capability name in _CAPABILITY_GROUPS must correspond to a
    real, currently-registered tool, or the mapping has drifted out of sync
    with the registry."""
    from src.tools.registry import all_tools

    real_tool_names = {t.name for t in all_tools()}
    for _group_title, entries in _CAPABILITY_GROUPS:
        for tool_name, _desc in entries:
            assert tool_name in real_tool_names, f"{tool_name!r} in _CAPABILITY_GROUPS is not a registered tool"


def test_handle_explain_capabilities_mentions_forwarded_message_suggestions():
    reply = _handle_explain_capabilities({"id": 1, "is_admin": False})
    assert "הודעה מועברת" in reply


# Tools that are deliberately NOT in "what can you do?": conversation plumbing, and tools only offered in a
# specific context (a pending draft / suggestion) or that describe the list itself.
_NOT_LISTED_ON_PURPOSE = {
    "chat", "unclear", "explain_capabilities", "respond_to_email_draft", "confirm_suggestion",
    "send_feature_request_to_developer",
}


def test_every_registered_tool_is_listed_in_capability_groups_or_explicitly_exempt():
    """The other direction of the drift check: a new tool that is not added to _CAPABILITY_GROUPS would silently
    be missing from 'what can you do?'. Add it there (or, if it truly should not be listed, to the set above)."""
    from src.tools.registry import all_tools

    listed = {name for _title, entries in _CAPABILITY_GROUPS for name, _desc in entries}
    missing = {t.name for t in all_tools()} - listed - _NOT_LISTED_ON_PURPOSE
    assert not missing, f"tools missing from _CAPABILITY_GROUPS: {sorted(missing)}"

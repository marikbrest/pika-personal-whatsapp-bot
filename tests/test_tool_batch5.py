"""
src.tools.batch5 - the fifth batch of the function-calling migration
(user_manage, zabbix_status, usage_status, email_read). The first batch
with admin_only=True tools - covers both defense layers: tools_for()
excluding them from a non-admin user's classification set, AND each
handler's own independent is_admin check still firing even if called
directly (the second layer every one of these handlers already had before
this migration, unchanged).
"""
from unittest.mock import MagicMock

import pytest

from src.tools.batch5 import (
    _validate_user_manage_args,
    email_read_tool,
    usage_status_tool,
    user_manage_tool,
    zabbix_status_tool,
)
from src.tools.dispatch import execute_tool
from src.tools.registry import tools_for


def test_admin_only_tools_are_excluded_for_non_admin_users():
    for tool in (zabbix_status_tool, usage_status_tool, user_manage_tool):
        assert tool not in tools_for({"is_admin": False})
        assert tool not in tools_for({})  # missing key treated as falsy


def test_admin_only_tools_are_included_for_admin_users():
    for tool in (zabbix_status_tool, usage_status_tool, user_manage_tool):
        assert tool in tools_for({"is_admin": True})


def test_email_read_tool_is_not_admin_only():
    assert email_read_tool in tools_for({"is_admin": False})


def test_zabbix_status_tool_dispatches_to_the_real_handler(monkeypatch):
    import src.tools.batch5 as batch5

    monkeypatch.setattr(batch5, "_handle_zabbix_status", lambda user: "infra status text")
    reply = execute_tool(zabbix_status_tool, {"id": 1, "is_admin": True}, {})
    assert reply == "infra status text"


def test_usage_status_tool_dispatches_to_the_real_handler(monkeypatch):
    import src.tools.batch5 as batch5

    monkeypatch.setattr(batch5, "_handle_usage_status", lambda user: "usage status text")
    reply = execute_tool(usage_status_tool, {"id": 1, "is_admin": True}, {})
    assert reply == "usage status text"


def test_email_read_tool_dispatches_to_the_real_handler(monkeypatch):
    import src.tools.batch5 as batch5

    monkeypatch.setattr(batch5, "_handle_email_read", lambda user, args: f"emails for query={args.get('query')}")
    reply = execute_tool(email_read_tool, {"id": 1}, {"query": "is:unread"})
    assert reply == "emails for query=is:unread"


def test_user_manage_tool_dispatches_to_the_real_handler(monkeypatch):
    import src.tools.batch5 as batch5

    monkeypatch.setattr(batch5, "_handle_user_manage", lambda user, args: f"user manage: {args['action']}")
    reply = execute_tool(user_manage_tool, {"id": 1, "is_admin": True}, {"action": "list"})
    assert reply == "user manage: list"


def test_handlers_own_admin_check_still_fires_even_if_called_directly(db_path, make_user):
    """Defense in depth, unchanged by this migration: even if tools_for()
    filtering were ever buggy, the handler itself still refuses a non-admin
    user - proven here against the REAL handlers, not mocked."""
    from src.webhook_handler import _handle_usage_status, _handle_user_manage, _handle_zabbix_status

    user_id = make_user(is_admin=False)
    non_admin_user = {"id": user_id, "is_admin": False}

    assert "מנהל" in _handle_zabbix_status(non_admin_user)
    assert "מנהל" in _handle_usage_status(non_admin_user)
    assert "מנהל" in _handle_user_manage(non_admin_user, {"action": "list"})


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"action": "list"}, True),
        ({"action": "add", "whatsapp_number": "972501234567"}, True),
        ({"action": "add"}, False),
        ({"action": "disable", "whatsapp_number": "972501234567"}, True),
        ({"action": "disable"}, False),
        ({"action": "bogus"}, False),
        ({}, False),
    ],
)
def test_validate_user_manage_args_matches_intent_parser_rules(args, expected):
    assert _validate_user_manage_args(args) is expected


def test_user_manage_description_distinguishes_from_add_contact():
    """The reverse side of the boundary batch 4's add_contact already
    carries - the same critical pair, from the other tool's perspective."""
    assert "add_contact" in user_manage_tool.description


def test_email_read_description_distinguishes_from_analyze_email():
    assert "analyze_email" in email_read_tool.description

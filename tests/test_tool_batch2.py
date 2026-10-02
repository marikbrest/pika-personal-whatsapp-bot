"""
src.tools.batch2 - the second batch of the function-calling migration
(drive, web_search, email_analyze, watch_manage). Same approach as
test_tool_pilot.py: handlers are thin wrappers around the existing
webhook_handler functions, so these tests focus on the WIRING (Tool ->
handler -> real function, correct schema shape, the transplanted validate
rules) rather than re-testing business logic already covered by
test_google_drive.py / test_gemini_search_web.py / test_gmail_thread_analysis.py
/ test_watchers.py.
"""
import pytest

from src.tools.batch2 import (
    _validate_drive_args,
    _validate_email_analyze_args,
    _validate_watch_manage_args,
    drive_tool,
    email_analyze_tool,
    watch_manage_tool,
    web_search_tool,
)
from src.tools.dispatch import execute_tool


def test_web_search_tool_dispatches_to_the_real_handler(monkeypatch):
    import src.tools.batch2 as batch2

    monkeypatch.setattr(batch2, "_handle_web_search", lambda args: f"search reply for {args['query']}")
    reply = execute_tool(web_search_tool, {}, {"query": "who won"})
    assert reply == "search reply for who won"


def test_drive_tool_dispatches_to_the_real_handler(monkeypatch):
    import src.tools.batch2 as batch2

    monkeypatch.setattr(batch2, "_handle_drive", lambda user, args: f"drive reply for {args['action']}")
    reply = execute_tool(drive_tool, {"id": 1}, {"action": "search", "query": "invoice"})
    assert reply == "drive reply for search"


def test_email_analyze_tool_dispatches_to_the_real_handler(monkeypatch):
    import src.tools.batch2 as batch2

    monkeypatch.setattr(batch2, "_handle_email_analyze", lambda user, args: f"email reply for {args['action']}")
    reply = execute_tool(email_analyze_tool, {"id": 1}, {"action": "unanswered"})
    assert reply == "email reply for unanswered"


def test_watch_manage_tool_dispatches_to_the_real_handler(monkeypatch):
    import src.tools.batch2 as batch2

    monkeypatch.setattr(batch2, "_handle_watch_manage", lambda user, args: f"watch reply for {args['action']}")
    reply = execute_tool(watch_manage_tool, {"id": 1}, {"action": "list"})
    assert reply == "watch reply for list"


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"action": "search", "query": "invoice"}, True),
        ({"action": "search"}, False),
        ({"action": "search", "query": ""}, False),
        ({"action": "save_note", "filename": "n.txt", "content": "x"}, True),
        ({"action": "save_note", "filename": "n.txt"}, False),
        ({"action": "save_note", "content": "x"}, False),
        ({"action": "bogus"}, False),
        ({}, False),
    ],
)
def test_validate_drive_args_matches_intent_parser_rules(args, expected):
    """Same table of cases as test_intent_parser_validation.py's drive block -
    this function is a verbatim transplant, and divergence would mean the
    migrated tool behaves differently from the old classifier for the same
    input."""
    assert _validate_drive_args(args) is expected


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"action": "summarize", "query": "dani project"}, True),
        ({"action": "summarize"}, False),
        ({"action": "summarize", "query": ""}, False),
        ({"action": "unanswered"}, True),
        ({"action": "bogus"}, False),
        ({}, False),
    ],
)
def test_validate_email_analyze_args_matches_intent_parser_rules(args, expected):
    assert _validate_email_analyze_args(args) is expected


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"action": "add", "watch_type": "email_reply", "query": "dani"}, True),
        ({"action": "add", "watch_type": "email_reply"}, False),
        ({"action": "add", "watch_type": "web_page", "url": "https://x.com"}, True),
        ({"action": "add", "watch_type": "web_page"}, False),
        ({"action": "add", "watch_type": "carrier_pigeon", "url": "https://x.com"}, False),
        ({"action": "add"}, False),
        ({"action": "list"}, True),
        ({"action": "cancel", "match": "x"}, True),
        ({"action": "cancel"}, False),
        ({"action": "bogus"}, False),
        ({}, False),
    ],
)
def test_validate_watch_manage_args_matches_intent_parser_rules(args, expected):
    assert _validate_watch_manage_args(args) is expected


def test_all_batch2_tool_descriptions_carry_an_explicit_boundary():
    """Regression guard for the pilot cutover's own first-day lesson: a tool
    that can plausibly be confused with another intent must say so in its
    own description, not just describe what it positively does."""
    for tool in (web_search_tool, drive_tool, email_analyze_tool, watch_manage_tool):
        assert "do not use this" in tool.description.lower(), tool.name

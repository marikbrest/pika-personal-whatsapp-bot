"""
src.tools.batch3 - the third batch of the function-calling migration
(calendar, saved_link, memory, morning_brief, semantic_search). Same
approach as test_tool_pilot.py / test_tool_batch2.py: focuses on the WIRING
(Tool -> handler -> real function, correct schema shape, the transplanted
validate rules), not re-testing business logic already covered elsewhere
(test_google_calendar_updates.py, etc.).
"""
import pytest

from src.tools.batch3 import (
    _validate_calendar_args,
    _validate_memory_args,
    _validate_saved_link_args,
    _validate_semantic_search_args,
    calendar_tool,
    memory_tool,
    morning_brief_tool,
    saved_link_tool,
    semantic_search_tool,
)
from src.tools.dispatch import execute_tool


def test_calendar_tool_dispatches_to_the_real_handler(monkeypatch):
    import src.tools.batch3 as batch3

    monkeypatch.setattr(batch3, "_handle_calendar", lambda user, args: f"calendar reply for {args['action']}")
    reply = execute_tool(calendar_tool, {"id": 1}, {"action": "query", "start": "s", "end": "e"})
    assert reply == "calendar reply for query"


def test_saved_link_tool_dispatches_to_the_real_handler(monkeypatch):
    import src.tools.batch3 as batch3

    monkeypatch.setattr(batch3, "_handle_saved_link", lambda user, args: f"link reply for {args['action']}")
    reply = execute_tool(saved_link_tool, {"id": 1}, {"action": "list"})
    assert reply == "link reply for list"


def test_memory_tool_dispatches_to_the_real_handler(monkeypatch):
    import src.tools.batch3 as batch3

    monkeypatch.setattr(batch3, "_handle_memory", lambda user, args: f"memory reply for {args['action']}")
    reply = execute_tool(memory_tool, {"id": 1}, {"action": "list"})
    assert reply == "memory reply for list"


def test_morning_brief_tool_dispatches_to_the_real_handler(monkeypatch):
    import src.tools.batch3 as batch3

    monkeypatch.setattr(batch3, "_handle_morning_brief", lambda user: "brief text")
    reply = execute_tool(morning_brief_tool, {"id": 1}, {})
    assert reply == "brief text"


def test_semantic_search_tool_dispatches_to_the_real_handler(monkeypatch):
    import src.tools.batch3 as batch3

    monkeypatch.setattr(batch3, "_handle_semantic_search", lambda user, args: f"search reply for {args['query']}")
    reply = execute_tool(semantic_search_tool, {"id": 1}, {"query": "what did I read"})
    assert reply == "search reply for what did I read"


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"action": "query", "start": "s", "end": "e"}, True),
        ({"action": "query"}, False),
        ({"action": "create", "start": "s", "end": "e", "summary": "x"}, True),
        ({"action": "create", "start": "s", "end": "e"}, False),
        ({"action": "update", "match": "m", "start": "s"}, True),
        ({"action": "update", "start": "s"}, False),  # no match
        ({"action": "update", "match": "m"}, False),  # nothing to change
        ({"action": "delete"}, False),
        ({}, False),
    ],
)
def test_validate_calendar_args_matches_intent_parser_rules(args, expected):
    assert _validate_calendar_args(args) is expected


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"action": "save", "url": "https://x.com"}, True),
        ({"action": "save"}, False),
        ({"action": "list"}, True),
        ({"action": "forget", "match": "x"}, True),
        ({"action": "forget"}, False),
        ({"action": "bogus"}, False),
        ({}, False),
    ],
)
def test_validate_saved_link_args_matches_intent_parser_rules(args, expected):
    assert _validate_saved_link_args(args) is expected


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"action": "save", "fact_key": "diet", "fact_value": "vegetarian"}, True),
        ({"action": "save", "fact_key": "diet"}, False),
        ({"action": "list"}, True),
        ({"action": "forget_all"}, True),
        ({"action": "forget", "fact_key": "diet"}, True),
        ({"action": "forget"}, False),
        ({"action": "bogus"}, False),
        ({}, False),
    ],
)
def test_validate_memory_args_matches_intent_parser_rules(args, expected):
    assert _validate_memory_args(args) is expected


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"query": "what did I read about X"}, True),
        ({"query": ""}, False),
        ({}, False),
    ],
)
def test_validate_semantic_search_args_matches_intent_parser_rules(args, expected):
    assert _validate_semantic_search_args(args) is expected


def test_ambiguous_batch3_tool_descriptions_carry_an_explicit_boundary():
    """Regression guard: this batch has real inter-tool overlap (save/watch/
    search all have close cousins in earlier batches) - each must say so."""
    for tool in (calendar_tool, saved_link_tool, memory_tool, semantic_search_tool):
        assert "do not use this" in tool.description.lower(), tool.name

"""
src.tools.pilot - the three stage-A tools (weather, market, task_manage).
Handlers are thin wrappers around the existing webhook_handler functions, so
these tests focus on the WIRING (Tool -> handler -> real function, correct
schema shape, the transplanted validate rule) rather than re-testing weather/
market/task_manage business logic already covered in
test_webhook_admin_gated_intents.py / test_task_manage.py / the markets
integration tests.
"""
import pytest

from src.tools.dispatch import execute_tool
from src.tools.pilot import _validate_task_manage_args, market_tool, task_manage_tool, weather_tool
from src.intent_parser import FALLBACK_REPLY


def test_weather_tool_dispatches_to_the_real_handler(monkeypatch):
    import src.tools.pilot as pilot

    monkeypatch.setattr(pilot, "_handle_weather", lambda args: f"weather reply for {args.get('location')}")
    reply = execute_tool(weather_tool, {}, {"location": "Tel Aviv", "day_offset": 0, "is_range": False})
    assert reply == "weather reply for Tel Aviv"


def test_market_tool_dispatches_to_the_real_handler(monkeypatch):
    import src.tools.pilot as pilot

    monkeypatch.setattr(pilot, "_handle_market", lambda args: f"quote for {args['symbol']}")
    reply = execute_tool(market_tool, {}, {"symbol": "AAPL"})
    assert reply == "quote for AAPL"


def test_task_manage_tool_dispatches_to_the_real_handler(db_path, make_user):
    """Uses the REAL _handle_task_manage (not mocked) against a real test DB -
    this is the one pilot tool with actual state, worth proving end to end."""
    user_id = make_user()
    reply = execute_tool(task_manage_tool, {"id": user_id}, {"action": "add", "content": "חלב", "list_name": None})
    assert "נוסף" in reply and "חלב" in reply


@pytest.mark.parametrize(
    "args,expected",
    [
        ({"action": "add", "content": "milk"}, True),
        ({"action": "add"}, False),
        ({"action": "add", "content": ""}, False),
        ({"action": "list"}, True),
        ({"action": "clear"}, True),
        ({"action": "done", "match": "milk"}, True),
        ({"action": "done"}, False),
        ({"action": "delete"}, False),
        ({"action": "bogus"}, False),
        ({}, False),
    ],
)
def test_validate_task_manage_args_matches_intent_parser_rules(args, expected):
    """Same table of cases as test_intent_parser_validation.py's task_manage
    block - this function is a verbatim transplant of those rules, and
    divergence between the two would mean the migrated tool behaves
    differently from today's classifier for the same input."""
    assert _validate_task_manage_args(args) is expected


def test_task_manage_tool_invalid_args_returns_fallback_without_touching_db(db_path, make_user):
    user_id = make_user()
    reply = execute_tool(task_manage_tool, {"id": user_id}, {"action": "add"})  # no content
    assert reply == FALLBACK_REPLY

    from src.db.models import list_tasks
    assert list_tasks(user_id) == []

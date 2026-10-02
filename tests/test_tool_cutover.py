"""
_classify_text_with_cutover (2026-09-14) - the real switch-over for the
function-calling migration's tools, started with the 3 pilot tools
(weather/market/task_manage) from Stage A/B. Mocks
classify_with_tools/execute_tool/tools_for directly rather than hitting the
real (billed) Gemini API - their own correctness is proven elsewhere
(test_gemini_adapter.py, test_tool_dispatch.py).
"""
from unittest.mock import MagicMock, patch

from src.webhook_handler import (
    _build_tools_context,
    _classify_text_with_cutover,
    _format_pending_draft_for_tools,
)


def _user():
    return {"id": 1, "timezone": "Asia/Jerusalem", "is_admin": False}


def test_pilot_tool_match_executes_the_real_tool_and_skips_the_old_classifier():
    weather_tool = MagicMock()
    weather_tool.name = "get_weather"

    with patch("src.tools.registry.tools_for", return_value=[weather_tool]), \
         patch("src.tools.gemini_adapter.classify_with_tools", return_value=("get_weather", {"day_offset": 0})), \
         patch("src.tools.dispatch.execute_tool", return_value="18 degrees and sunny") as mock_execute, \
         patch("src.webhook_handler.parse_message") as mock_old_classifier:
        result = _classify_text_with_cutover("מה מזג האוויר", _user(), None, None, None, None)

    mock_old_classifier.assert_not_called()
    mock_execute.assert_called_once()
    assert result == {"intent": "get_weather", "get_weather": {"day_offset": 0}, "reply": "18 degrees and sunny"}


def test_chat_result_falls_back_to_the_old_classifier():
    chat_tool = MagicMock()
    chat_tool.name = "chat"

    with patch("src.tools.registry.tools_for", return_value=[chat_tool]), \
         patch("src.tools.gemini_adapter.classify_with_tools", return_value=("chat", {"reply_text": "hi"})), \
         patch("src.tools.dispatch.execute_tool") as mock_execute, \
         patch("src.webhook_handler.parse_message", return_value={"intent": "reminder", "reply": "ok"}) as mock_old:
        result = _classify_text_with_cutover("תזכיר לי לקנות חלב מחר", _user(), None, None, None, None)

    mock_execute.assert_not_called()  # chat/unclear from the pilot set is never trusted or executed directly
    mock_old.assert_called_once()
    assert result == {"intent": "reminder", "reply": "ok"}


def test_unclear_result_falls_back_to_the_old_classifier():
    with patch("src.tools.registry.tools_for", return_value=[]), \
         patch("src.tools.gemini_adapter.classify_with_tools", return_value=("unclear", {})), \
         patch("src.webhook_handler.parse_message", return_value={"intent": "drive", "reply": "ok"}) as mock_old:
        result = _classify_text_with_cutover("תחפש לי את הקובץ", _user(), None, None, None, None)

    mock_old.assert_called_once()
    assert result["intent"] == "drive"


def test_pilot_classification_failure_falls_back_to_the_old_classifier():
    """A Gemini error on the small pilot call must not take down the whole
    message - it just means falling back to the exact behavior every message
    had before cutover."""
    with patch("src.tools.registry.tools_for", return_value=[]), \
         patch("src.tools.gemini_adapter.classify_with_tools", side_effect=RuntimeError("network error")), \
         patch("src.webhook_handler.parse_message", return_value={"intent": "chat", "reply": "ok"}) as mock_old:
        result = _classify_text_with_cutover("hello", _user(), None, None, None, None)

    mock_old.assert_called_once()
    assert result["intent"] == "chat"


def _pending_draft():
    return {"id": 1, "to_address": "x@example.com", "subject": "s", "body": "b"}


def test_respond_to_email_draft_is_excluded_from_the_candidate_tools_when_no_draft_is_pending():
    """Batch 6 (2026-09-14): the same 'physical exclusion is real defence'
    principle registry.tools_for() already applies to admin_only tools,
    just keyed on draft state - Gemini must not even be offered
    respond_to_email_draft when there is nothing pending to respond to."""
    weather_tool = MagicMock()
    weather_tool.name = "get_weather"
    respond_tool = MagicMock()
    respond_tool.name = "respond_to_email_draft"

    with patch("src.tools.registry.tools_for", return_value=[weather_tool, respond_tool]), \
         patch("src.tools.gemini_adapter.classify_with_tools", return_value=("get_weather", {})) as mock_classify, \
         patch("src.tools.dispatch.execute_tool", return_value="18 degrees"):
        _classify_text_with_cutover("מה מזג האוויר", _user(), None, None, None, None)

    offered_tools = mock_classify.call_args.args[1]
    assert respond_tool not in offered_tools
    assert weather_tool in offered_tools


def test_respond_to_email_draft_is_offered_and_context_is_prepended_when_a_draft_is_pending():
    respond_tool = MagicMock()
    respond_tool.name = "respond_to_email_draft"
    pending_draft = _pending_draft()

    with patch("src.tools.registry.tools_for", return_value=[respond_tool]), \
         patch("src.tools.gemini_adapter.classify_with_tools", return_value=("respond_to_email_draft", {"action": "send"})) as mock_classify, \
         patch("src.tools.dispatch.execute_tool", return_value="נשלח"):
        _classify_text_with_cutover("כן, תשלח", _user(), None, None, pending_draft, None)

    offered_tools = mock_classify.call_args.args[1]
    assert respond_tool in offered_tools
    contents_sent = mock_classify.call_args.args[0]
    assert pending_draft["to_address"] in contents_sent
    assert pending_draft["subject"] in contents_sent
    assert "כן, תשלח" in contents_sent


def test_format_pending_draft_for_tools_includes_all_draft_fields():
    block = _format_pending_draft_for_tools(_pending_draft())
    assert "x@example.com" in block
    assert "s" in block
    assert "b" in block
    assert "respond_to_email_draft" in block


def test_conversation_history_is_included_in_the_context_sent_to_gemini():
    """Batch 7 (2026-09-14): the context gap that made a follow-up like
    'and tomorrow?' unresolvable on the new pipeline through batches 1-6."""
    weather_tool = MagicMock()
    weather_tool.name = "get_weather"
    history = [{"direction": "incoming", "raw_content": "מה מזג האוויר בתל אביב"}]

    with patch("src.tools.registry.tools_for", return_value=[weather_tool]), \
         patch("src.tools.gemini_adapter.classify_with_tools", return_value=("get_weather", {})) as mock_classify, \
         patch("src.tools.dispatch.execute_tool", return_value="ok"):
        _classify_text_with_cutover("ומחר?", _user(), history, None, None, None)

    contents_sent = mock_classify.call_args.args[0]
    assert "מה מזג האוויר בתל אביב" in contents_sent
    assert "ומחר?" in contents_sent


def test_contacts_and_facts_are_included_in_the_context_sent_to_gemini():
    weather_tool = MagicMock()
    weather_tool.name = "get_weather"
    contacts = [{"name": "Mom"}]
    facts = [{"fact_key": "diet", "fact_value": "צמחוני"}]

    with patch("src.tools.registry.tools_for", return_value=[weather_tool]), \
         patch("src.tools.gemini_adapter.classify_with_tools", return_value=("get_weather", {})) as mock_classify, \
         patch("src.tools.dispatch.execute_tool", return_value="ok"):
        _classify_text_with_cutover("מה מזג האוויר", _user(), None, contacts, None, facts)

    contents_sent = mock_classify.call_args.args[0]
    assert "Mom" in contents_sent
    assert "צמחוני" in contents_sent


def test_build_tools_context_with_no_history_contacts_or_facts_has_no_placeholder_noise():
    """No history/facts means those blocks should just be absent, not
    rendered as empty noise in every single classify_with_tools call."""
    context = _build_tools_context(_user(), None, None, None, None)
    assert "השיחה עד כה" not in context
    assert "אין עדיין אנשי קשר שמורים" in context  # contacts always renders something, even when empty


def test_build_tools_context_omits_the_old_classifiers_pending_draft_wording():
    """The one deliberate non-reuse from intent_parser._build_header: its own
    pending-draft block tells Gemini to classify as the OLD intent name
    'email_action', which does not exist as a tool here and would mislead
    the new pipeline - _format_pending_draft_for_tools is used instead."""
    context = _build_tools_context(_user(), None, None, _pending_draft(), None)
    assert "email_action" not in context
    assert "respond_to_email_draft" in context


def test_confirm_suggestion_is_excluded_from_the_candidate_tools_when_no_suggestion_is_pending():
    """Batch 9 (2026-09-14): same physical-exclusion defence as
    respond_to_email_draft, keyed on pending_suggestion instead of
    pending_draft."""
    weather_tool = MagicMock()
    weather_tool.name = "get_weather"
    confirm_tool = MagicMock()
    confirm_tool.name = "confirm_suggestion"

    with patch("src.tools.registry.tools_for", return_value=[weather_tool, confirm_tool]), \
         patch("src.tools.gemini_adapter.classify_with_tools", return_value=("get_weather", {})) as mock_classify, \
         patch("src.tools.dispatch.execute_tool", return_value="18 degrees"):
        _classify_text_with_cutover("מה מזג האוויר", _user(), None, None, None, None, None)

    offered_tools = mock_classify.call_args.args[1]
    assert confirm_tool not in offered_tools
    assert weather_tool in offered_tools


def test_confirm_suggestion_is_offered_and_context_is_prepended_when_a_suggestion_is_pending():
    confirm_tool = MagicMock()
    confirm_tool.name = "confirm_suggestion"
    pending_suggestion = {"id": 1, "confirmation_text": "רוצה שאוסיף אירוע מחר?"}

    with patch("src.tools.registry.tools_for", return_value=[confirm_tool]), \
         patch(
             "src.tools.gemini_adapter.classify_with_tools",
             return_value=("confirm_suggestion", {"action": "confirm"}),
         ) as mock_classify, \
         patch("src.tools.dispatch.execute_tool", return_value="בוצע"):
        _classify_text_with_cutover("כן", _user(), None, None, None, None, pending_suggestion)

    offered_tools = mock_classify.call_args.args[1]
    assert confirm_tool in offered_tools
    contents_sent = mock_classify.call_args.args[0]
    assert "רוצה שאוסיף אירוע מחר?" in contents_sent
    assert "confirm_suggestion" in contents_sent


def test_pilot_tool_name_not_found_in_registry_falls_back():
    """Defensive: if classify_with_tools somehow returns a tool name not in
    the list tools_for() gave it, that must not crash - fall back cleanly."""
    with patch("src.tools.registry.tools_for", return_value=[]), \
         patch("src.tools.gemini_adapter.classify_with_tools", return_value=("get_weather", {})), \
         patch("src.webhook_handler.parse_message", return_value={"intent": "chat", "reply": "ok"}) as mock_old:
        result = _classify_text_with_cutover("weather?", _user(), None, None, None, None)

    mock_old.assert_called_once()
    assert result["intent"] == "chat"

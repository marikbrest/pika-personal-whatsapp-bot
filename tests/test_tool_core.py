"""src.tools.core - chat/unclear, the universal catch-all tools every ANY-mode
classification call must have available alongside domain tools."""
from src.tools.core import chat_tool, unclear_tool
from src.tools.dispatch import execute_tool
from src.intent_parser import FALLBACK_REPLY


def test_chat_tool_uses_reply_text_and_has_no_side_effect():
    reply = execute_tool(chat_tool, {}, {"reply_text": "שלום, מה שלומך?"})
    assert reply == "שלום, מה שלומך?"


def test_chat_tool_schema_requires_reply_text():
    assert chat_tool.parameters["required"] == ["reply_text"]
    assert not chat_tool.renders_own_reply


def test_unclear_tool_always_returns_the_fallback_reply_regardless_of_args():
    assert execute_tool(unclear_tool, {}, {}) == FALLBACK_REPLY
    assert execute_tool(unclear_tool, {}, {"whatever": "extra args are ignored"}) == FALLBACK_REPLY


def test_unclear_tool_takes_no_required_parameters():
    assert unclear_tool.parameters.get("required", []) == []

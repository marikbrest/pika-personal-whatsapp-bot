"""src.tools.dispatch.execute_tool - the piece that turns a (tool, args) pair
into the actual reply text, honoring renders_own_reply and validate."""
from src.tools.registry import Tool
from src.tools.dispatch import execute_tool
from src.intent_parser import FALLBACK_REPLY


def test_renders_own_reply_true_uses_the_handlers_return_value():
    t = Tool(name="_d1", description="d", parameters={"type": "object", "properties": {}},
              handler=lambda u, a: "the real computed reply")
    assert execute_tool(t, {}, {}) == "the real computed reply"


def test_renders_own_reply_true_handler_exception_falls_back_gracefully():
    def boom(u, a):
        raise RuntimeError("integration is down")
    t = Tool(name="_d2", description="d", parameters={"type": "object", "properties": {}}, handler=boom)
    reply = execute_tool(t, {}, {})
    assert "בעיה" in reply or "נסות" in reply  # a graceful Hebrew fallback, not a traceback


def test_renders_own_reply_true_none_return_falls_back_to_fallback_reply():
    t = Tool(name="_d3", description="d", parameters={"type": "object", "properties": {}}, handler=lambda u, a: None)
    assert execute_tool(t, {}, {}) == FALLBACK_REPLY


def test_renders_own_reply_false_uses_reply_text_from_args():
    calls = []
    t = Tool(
        name="_d4", description="d",
        parameters={"type": "object", "properties": {"reply_text": {"type": "string"}}, "required": ["reply_text"]},
        handler=lambda u, a: calls.append((u, a)),
        renders_own_reply=False,
    )
    reply = execute_tool(t, {"id": 1}, {"reply_text": "בסדר, נשמר.", "content": "x"})
    assert reply == "בסדר, נשמר."
    assert calls == [({"id": 1}, {"reply_text": "בסדר, נשמר.", "content": "x"})]  # side effect still ran


def test_renders_own_reply_false_missing_reply_text_falls_back():
    """Schema compliance from Gemini is not literally guaranteed - same
    reasoning intent_parser._validate_result already applies everywhere."""
    t = Tool(
        name="_d5", description="d",
        parameters={"type": "object", "properties": {"reply_text": {"type": "string"}}, "required": ["reply_text"]},
        handler=lambda u, a: None, renders_own_reply=False,
    )
    assert execute_tool(t, {}, {}) == FALLBACK_REPLY


def test_renders_own_reply_false_handler_exception_still_sends_reply_text():
    """The side effect failing must not swallow a reply the user is owed -
    same spirit as scheduler's per-item error isolation elsewhere in this
    codebase."""
    def boom(u, a):
        raise RuntimeError("db write failed")
    t = Tool(
        name="_d6", description="d",
        parameters={"type": "object", "properties": {"reply_text": {"type": "string"}}, "required": ["reply_text"]},
        handler=boom, renders_own_reply=False,
    )
    assert execute_tool(t, {}, {"reply_text": "טקסט התגובה"}) == "טקסט התגובה"


def test_validate_false_returns_fallback_without_calling_handler():
    called = []
    t = Tool(
        name="_d7", description="d", parameters={"type": "object", "properties": {}},
        handler=lambda u, a: called.append(1) or "should not be seen",
        validate=lambda args: False,
    )
    assert execute_tool(t, {}, {}) == FALLBACK_REPLY
    assert called == []


def test_validate_true_lets_the_handler_run():
    t = Tool(
        name="_d8", description="d", parameters={"type": "object", "properties": {}},
        handler=lambda u, a: "ran", validate=lambda args: True,
    )
    assert execute_tool(t, {}, {}) == "ran"

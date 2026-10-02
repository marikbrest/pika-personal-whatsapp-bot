"""
src.tools.gemini_adapter - the only file that touches google.genai types.
_to_function_declaration is pure and tested directly. classify_with_tools is
tested against a hand-built fake client rather than the real API: the real
shapes it relies on (parameters_json_schema accepted as a plain dict, ANY
mode, response.function_calls, no accompanying text) were verified live once
against the real installed SDK this session (smoke_function_call.py /
smoke_reply_arg.py, not checked in) - not something to re-verify with a real,
billed API call on every test run.
"""
from unittest.mock import MagicMock

from google.genai import types

from src.tools.gemini_adapter import _to_function_declaration, classify_with_tools
from src.tools.registry import Tool


def _tool(name="_ga_tool") -> Tool:
    return Tool(
        name=name, description="a test tool",
        parameters={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
        handler=lambda u, a: "unused",
    )


def test_to_function_declaration_carries_name_description_and_schema():
    decl = _to_function_declaration(_tool())
    assert decl.name == "_ga_tool"
    assert decl.description == "a test tool"
    assert decl.parameters_json_schema == {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}


class _FakeFunctionCall:
    def __init__(self, name, args):
        self.name = name
        self.args = args


class _FakeResponse:
    def __init__(self, function_calls):
        self.function_calls = function_calls
        self.usage_metadata = None  # short-circuits _log_usage_safe cleanly, no DB touched


def test_classify_with_tools_returns_name_and_args_on_success(monkeypatch):
    import src.tools.gemini_adapter as adapter

    fake_response = _FakeResponse([_FakeFunctionCall("chat", {"reply_text": "hi"})])
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = fake_response
    monkeypatch.setattr(adapter, "_get_client", lambda: fake_client)

    result = classify_with_tools("hello", [_tool("chat")])
    assert result == ("chat", {"reply_text": "hi"})


def test_classify_with_tools_passes_any_mode_and_the_right_tool_names(monkeypatch):
    import src.tools.gemini_adapter as adapter

    fake_response = _FakeResponse([_FakeFunctionCall("weather", {})])
    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = fake_response
    monkeypatch.setattr(adapter, "_get_client", lambda: fake_client)

    classify_with_tools("hello", [_tool("weather"), _tool("market")])

    _, kwargs = fake_client.models.generate_content.call_args
    config = kwargs["config"]
    assert config.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.ANY
    assert set(config.tool_config.function_calling_config.allowed_function_names) == {"weather", "market"}


def test_classify_with_tools_returns_none_when_no_function_call_comes_back(monkeypatch):
    import src.tools.gemini_adapter as adapter

    fake_client = MagicMock()
    fake_client.models.generate_content.return_value = _FakeResponse([])
    monkeypatch.setattr(adapter, "_get_client", lambda: fake_client)

    assert classify_with_tools("hello", [_tool()]) is None


def test_classify_with_tools_returns_none_on_api_exception(monkeypatch):
    import src.tools.gemini_adapter as adapter

    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = RuntimeError("network down")
    monkeypatch.setattr(adapter, "_get_client", lambda: fake_client)

    assert classify_with_tools("hello", [_tool()]) is None

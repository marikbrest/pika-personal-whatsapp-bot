"""Provider layer (src/ai.py + src/integrations/openai.py): no paid API calls, no cross-user leaks, no duplicate actions."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src import config
from src.ai import PROVIDERS, current_provider, manage_provider, provider_command, use_provider, use_user
from src.db.models import admin_get_user, get_ai_provider, set_ai_provider
from src.integrations import openai as adapter


@pytest.fixture()
def openai_configured(monkeypatch):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "fake-key")
    monkeypatch.setattr(config, "OPENAI_MODEL", "test-model")


def test_preferences_are_persistent_and_isolated(db_path, make_user):
    a = admin_get_user(make_user(whatsapp_number="972500000081"))
    b = admin_get_user(make_user(whatsapp_number="972500000082"))
    set_ai_provider(a["id"], "openai")
    assert get_ai_provider(a["id"]) == "openai"
    assert get_ai_provider(b["id"]) is None
    with use_user(a):
        assert current_provider() == "openai"
        with use_user(b):
            assert current_provider() == "gemini"
        assert current_provider() == "openai"
    assert current_provider() == "gemini"


def test_a_stored_provider_that_no_longer_exists_falls_back_to_the_default(db_path, make_user):
    user = admin_get_user(make_user())
    set_ai_provider(user["id"], "removed-provider")
    with use_user(user):
        assert current_provider() == "gemini"


def test_request_context_is_reset_on_error_and_isolated_between_threads():
    def read(provider):
        with use_provider(provider):
            return current_provider()
    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(read, ["openai", "gemini"])) == ["openai", "gemini"]
    with pytest.raises(RuntimeError):
        with use_provider("openai"):
            raise RuntimeError()
    assert current_provider() == "gemini"
    with pytest.raises(ValueError):
        with use_provider("nope"):
            pass


def test_commands_work_without_a_model_and_refuse_an_unconfigured_provider(db_path, make_user, monkeypatch):
    user = admin_get_user(make_user())
    assert "עדיין לא מוגדר" in provider_command("עבור ל־OpenAI", user)
    assert get_ai_provider(user["id"]) is None
    monkeypatch.setattr(config, "OPENAI_API_KEY", "fake-key")
    assert "עדיין לא מוגדר" in provider_command("switch to openai", user)  # key but no model name = still unconfigured
    monkeypatch.setattr(config, "OPENAI_MODEL", "test-model")
    assert "OpenAI" in provider_command("switch to OpenAI", user)
    assert get_ai_provider(user["id"]) == "openai"
    assert "OpenAI" in provider_command("באיזה ספק אני משתמש?", user)
    assert provider_command("מישהו כתב עבור ל־Gemini", user) is None  # a sentence containing a command is not a command
    assert get_ai_provider(user["id"]) == "openai"
    assert "Gemini" in provider_command("עבור ל־Gemini", user)
    assert get_ai_provider(user["id"]) == "gemini"


def test_status_names_the_model_and_other_available_providers(db_path, make_user, openai_configured):
    user = admin_get_user(make_user())
    reply = manage_provider(user, {"action": "status"})
    assert "Gemini" in reply and config.GEMINI_MODEL in reply and "OpenAI" in reply


def test_registry_is_the_single_list_of_providers():
    assert set(PROVIDERS) == {"gemini", "openai"}
    for spec in PROVIDERS.values():
        assert spec.aliases and spec.label


def test_strict_schema_does_not_mutate_registry_and_preserves_optional_omission():
    original = {"type": "object", "properties": {"action": {"type": "string"},
                "nested": {"type": "object", "properties": {"n": {"type": "integer"}}}}, "required": ["action"]}
    saved = deepcopy(original)
    converted = adapter._strict_schema(original)
    assert original == saved
    assert converted["additionalProperties"] is False
    assert converted["required"] == ["action", "nested"]
    assert converted["properties"]["nested"]["anyOf"][0]["additionalProperties"] is False
    assert adapter._without_nulls({"action": "status", "nested": None}) == {"action": "status"}


def _tool(name="example"):
    return SimpleNamespace(name=name, description="Test tool", parameters={"type": "object", "properties": {"optional": {"type": "string"}}})


def test_responses_disable_storage_and_parallel_tools(monkeypatch, openai_configured):
    client = Mock()
    client.post.return_value.json.return_value = {"status": "completed", "model": "m", "output": [
        {"type": "function_call", "name": "example", "arguments": '{"optional":null}'}]}
    with patch.object(adapter, "_get_client", return_value=client), patch.object(adapter, "_log_usage"):
        assert adapter.classify_with_tools("prompt", [_tool()]) == ("example", {})
    payload = client.post.call_args.kwargs["json"]
    assert payload["store"] is False and payload["parallel_tool_calls"] is False
    assert payload["tool_choice"] == "required" and payload["tools"][0]["strict"] is True
    assert payload["model"] == "test-model"


@pytest.mark.parametrize("output", [
    [], [{"type": "function_call", "name": "not_allowed", "arguments": "{}"}],
    [{"type": "function_call", "name": "example", "arguments": "{}"}] * 2,
    [{"type": "function_call", "name": "example", "arguments": "[]"}],
])
def test_invalid_tool_responses_are_never_executed(output):
    with patch.object(adapter, "_responses", return_value={"output": output}):
        assert adapter.classify_with_tools("prompt", [_tool()]) is None


def test_tool_arguments_reject_wrong_types_and_unknown_fields():
    schema = {"type": "object", "properties": {"count": {"type": "integer"}}, "required": ["count"]}
    assert adapter._valid_args({"count": 2}, schema)
    assert not adapter._valid_args({"count": True}, schema)
    assert not adapter._valid_args({"count": "2"}, schema)
    assert not adapter._valid_args({"count": 2, "unexpected": "value"}, schema)


def test_the_four_entry_points_hand_over_to_the_selected_provider():
    from src.integrations import gemini
    from src.tools.gemini_adapter import classify_with_tools

    with use_provider("openai"), \
         patch.object(adapter, "call_json", return_value={"a": 1}) as cj, \
         patch.object(adapter, "call_json_with_media", return_value={"b": 2}) as cm, \
         patch.object(adapter, "search_web", return_value={"answer": "x", "sources": []}) as sw, \
         patch.object(adapter, "classify_with_tools", return_value=("chat", {})) as ct:
        assert gemini.call_gemini_json("p") == {"a": 1}
        assert gemini.call_gemini_json_with_media("p", b"x", "image/png") == {"b": 2}
        assert gemini.search_web("q")["answer"] == "x"
        assert classify_with_tools("c", []) == ("chat", {})
    for m in (cj, cm, sw, ct):
        m.assert_called_once()


def test_gemini_is_untouched_when_it_is_the_provider():
    from src.integrations import gemini

    with patch.object(adapter, "call_json") as openai_call, patch.object(gemini, "_call_with_retry", return_value={"ok": 1}) as g:
        assert gemini.call_gemini_json("p") == {"ok": 1}
    g.assert_called_once()
    openai_call.assert_not_called()


def test_provider_failure_does_not_reclassify_with_the_legacy_classifier(db_path, make_user):
    from src.webhook_handler import _classify_text_with_cutover

    user = admin_get_user(make_user())
    with use_provider("openai"), patch("src.tools.gemini_adapter.classify_with_tools", return_value=None), \
         patch("src.webhook_handler.parse_message") as legacy:
        result = _classify_text_with_cutover("hello", user, [], [], None, [])
    legacy.assert_not_called()
    assert result["tool_executed"] is True and "OpenAI" in result["reply"]


def test_chat_reply_from_the_selected_provider_is_used_without_a_second_classification(db_path, make_user):
    from src.webhook_handler import _classify_text_with_cutover

    with use_provider("openai"), \
         patch("src.tools.gemini_adapter.classify_with_tools", return_value=("chat", {"reply_text": "שלום"})), \
         patch("src.webhook_handler.parse_message") as legacy:
        result = _classify_text_with_cutover("hello", admin_get_user(make_user()), [], [], None, [])
    assert result["reply"] == "שלום"
    legacy.assert_not_called()


def test_a_forwarded_message_cannot_change_the_provider(db_path, make_user):
    from src.webhook_handler import _classify_text_with_cutover

    user = admin_get_user(make_user())
    with use_provider("openai"), patch(
        "src.tools.gemini_adapter.classify_with_tools", return_value=("manage_ai_provider", {"action": "set", "provider": "gemini"}),
    ) as classify:
        result = _classify_text_with_cutover("עבור ל־Gemini", user, [], [], None, [], allow_provider_change=False)
    assert get_ai_provider(user["id"]) is None
    assert all(tool.name != "manage_ai_provider" for tool in classify.call_args.args[1])
    assert result["intent"] == "unclear"


def test_whole_message_pipeline_scopes_the_senders_provider(client, make_user):
    """End to end through the webhook: user A on OpenAI never leaks into user B's request."""
    from .conftest import post_webhook, whatsapp_text_payload

    a = admin_get_user(make_user(whatsapp_number="972500000091"))
    make_user(whatsapp_number="972500000092")
    set_ai_provider(a["id"], "openai")
    seen = []

    def fake_classify(contents, tools):
        seen.append(current_provider())
        return ("chat", {"reply_text": "ok"})

    with patch("src.tools.gemini_adapter.classify_with_tools", side_effect=fake_classify), \
         patch("src.webhook_handler.send_text_message", return_value=True):
        post_webhook(client, whatsapp_text_payload("972500000091", "hi", message_id="wamid.A"))
        post_webhook(client, whatsapp_text_payload("972500000092", "hi", message_id="wamid.B"))
    assert seen == ["openai", "gemini"]
    assert current_provider() == "gemini"


def test_provider_command_in_a_real_message_switches_without_calling_a_model(client, make_user, openai_configured):
    from .conftest import post_webhook, whatsapp_text_payload

    user = admin_get_user(make_user(whatsapp_number="972500000091"))
    with patch("src.tools.gemini_adapter.classify_with_tools") as classify, \
         patch("src.webhook_handler.send_text_message", return_value=True) as send:
        post_webhook(client, whatsapp_text_payload("972500000091", "עבור ל־OpenAI"))
    classify.assert_not_called()
    assert get_ai_provider(user["id"]) == "openai"
    assert "OpenAI" in send.call_args.kwargs["body"]


def test_proactive_assessment_uses_the_owners_provider(db_path, make_user):
    from src.proactive import assess_situation

    user = admin_get_user(make_user())
    set_ai_provider(user["id"], "openai")
    with patch.object(adapter, "call_json", return_value={"interrupt": False}) as api:
        assess_situation(user, "event", "calendar_new", [])
    api.assert_called_once()
    assert current_provider() == "gemini"


def test_post_errors_do_not_expose_secrets(capsys):
    with patch.object(adapter, "_get_client", side_effect=RuntimeError("secret-password private prompt")):
        assert adapter._post("responses", json={}) is None
    assert "secret-password" not in capsys.readouterr().out


def test_incomplete_responses_and_non_object_json_are_rejected():
    with patch.object(adapter, "_post", return_value={"status": "incomplete", "output": [], "usage": {}}), \
         patch.object(adapter, "_log_usage") as usage:
        assert adapter._responses("test") is None
    usage.assert_called_once()
    with patch.object(adapter, "_responses", return_value={"output": [{"type": "message", "content": [{"type": "output_text", "text": "[]"}]}]}):
        assert adapter.call_json("JSON please") is None


def test_media_is_encoded_inline_and_audio_is_transcribed_first():
    with patch.object(adapter, "call_json", return_value={"text": "ok"}) as json_call:
        adapter.call_json_with_media("JSON prompt", b"pdf", "application/pdf")
        assert json_call.call_args.args[1][0]["content"][1]["type"] == "input_file"
        assert adapter.call_json_with_media("p", b"x", "application/zip") is None
    with patch.object(adapter, "_post", return_value={"text": "תמלול"}) as audio, \
         patch.object(adapter, "call_json", return_value={"text": "ok"}) as json_call, \
         patch("src.db.models.log_ai_usage") as log:
        adapter.call_json_with_media("JSON prompt", b"ogg", "audio/ogg; codecs=opus")
        assert audio.call_args.args[0] == "audio/transcriptions"
        assert "תמלול" in json_call.call_args.args[0]
        assert log.call_args.kwargs["cost_unknown"] is True


# ---- usage and cost --------------------------------------------------------------------------------------

def test_cost_is_computed_from_operator_prices_with_cache_and_web_calls(db_path, monkeypatch):
    from src.db.models import log_ai_usage
    from src.integrations.ai_costs import openai_month_cost, openai_report

    monkeypatch.setattr(config, "OPENAI_MODEL", "test-model")
    monkeypatch.setattr(config, "OPENAI_PRICE_INPUT_PER_M", 1.0)
    monkeypatch.setattr(config, "OPENAI_PRICE_CACHED_INPUT_PER_M", 0.1)
    monkeypatch.setattr(config, "OPENAI_PRICE_OUTPUT_PER_M", 4.0)
    monkeypatch.setattr(config, "OPENAI_PRICE_WEB_SEARCH_PER_CALL", 0.01)
    log_ai_usage("openai", "test-model-2026-01-01", 1000, 100, 200, 1)
    log_ai_usage("openai", "some-other-model", 10, 10)
    cost, unknown = openai_month_cost()
    assert cost == pytest.approx((800 * 1.0 + 200 * 0.1 + 100 * 4.0) / 1_000_000 + 0.01)
    assert unknown == 1
    assert "אינו מלא" in openai_report()


def test_without_configured_prices_everything_is_unpriced_not_free(db_path):
    from src.db.models import log_ai_usage
    from src.integrations.ai_costs import openai_month_cost, openai_report

    log_ai_usage("openai", "test-model", 1000, 100)
    assert openai_month_cost() == (0.0, 1)
    assert "אינו מלא" in openai_report()


def test_no_openai_usage_means_no_change_to_the_report(db_path):
    from src.integrations.ai_costs import openai_report

    assert openai_report() == ""

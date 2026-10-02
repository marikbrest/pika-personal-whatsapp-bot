"""
src.tools.registry - the provider-neutral scaffolding stage A of the
function-calling migration is built on. See src/tools/registry.py's module
docstring for the two live-verified Gemini-API decisions this encodes.
"""
import pytest

import src.tools  # noqa: F401 - populates the registry as a side effect of import
from src.tools.registry import Tool, all_tools, get_tool, register, tools_for


def test_importing_src_tools_registers_the_expected_tools():
    names = {t.name for t in all_tools()}
    assert {
        "chat", "unclear", "get_weather", "get_market_quote", "manage_tasks",
        "web_search", "manage_drive", "analyze_email", "manage_watches",
        "manage_calendar", "manage_saved_links", "manage_memory", "get_morning_brief", "search_history",
        "create_reminder", "add_contact", "connect_google",
        "get_infra_status", "get_usage_status", "manage_bot_users", "read_emails",
        "draft_email", "respond_to_email_draft", "manage_reminders", "track_package",
        "confirm_suggestion", "explain_capabilities",
        "generate_image", "edit_image", "send_feature_request_to_developer",
        "explain_privacy", "manage_my_data",
        "manage_proactive_settings", "manage_vip_senders",
    } <= names


def test_get_tool_returns_none_for_unknown_name():
    assert get_tool("this_tool_does_not_exist") is None


def test_registering_the_same_name_twice_raises():
    t = Tool(name="_test_dup_tool", description="d", parameters={"type": "object", "properties": {}}, handler=lambda u, a: "x")
    register(t)
    try:
        with pytest.raises(ValueError):
            register(t)
    finally:
        import src.tools.registry as registry_module
        registry_module._REGISTRY.pop("_test_dup_tool", None)


def test_renders_own_reply_false_requires_reply_text_in_schema():
    """The exact bug this guards against: a tool that relies on Gemini's own
    generated text but never asked the schema for it - the reply would come
    back empty every time, silently."""
    with pytest.raises(AssertionError):
        Tool(
            name="_test_bad_tool",
            description="d",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda u, a: None,
            renders_own_reply=False,
        )


def test_renders_own_reply_false_with_reply_text_required_is_fine():
    t = Tool(
        name="_test_ok_tool",
        description="d",
        parameters={"type": "object", "properties": {"reply_text": {"type": "string"}}, "required": ["reply_text"]},
        handler=lambda u, a: None,
        renders_own_reply=False,
    )
    assert t.name == "_test_ok_tool"


def test_tools_for_excludes_admin_only_tools_for_non_admin_user():
    admin_tool = Tool(
        name="_test_admin_tool", description="d", parameters={"type": "object", "properties": {}},
        handler=lambda u, a: "x", admin_only=True,
    )
    register(admin_tool)
    try:
        assert admin_tool in tools_for({"is_admin": True})
        assert admin_tool not in tools_for({"is_admin": False})
        assert admin_tool not in tools_for({})  # missing key treated as falsy, not an error
    finally:
        import src.tools.registry as registry_module
        registry_module._REGISTRY.pop("_test_admin_tool", None)


def test_tools_for_includes_non_admin_tools_for_everyone():
    weather = get_tool("get_weather")
    assert weather in tools_for({"is_admin": True})
    assert weather in tools_for({"is_admin": False})


def test_tools_for_works_with_a_real_sqlite_row_not_just_a_plain_dict(db_path, make_user):
    """Regression test for a real bug (found 2026-09-14 by the test suite
    itself, not by inspection): tools_for() used to call user.get("is_admin")
    directly, which crashes with AttributeError on a real sqlite3.Row (the
    actual type every production caller passes, from
    get_user_by_whatsapp_number - Row supports row["key"] but has no .get()
    at all). This was latent since Stage A because every test used a plain
    dict, and the `or` short-circuited on `not t.admin_only` for every tool
    until batch 5 registered the first admin_only=True tool - only then did
    the right-hand side actually get evaluated against a real row."""
    from src.db.models import get_user_by_whatsapp_number

    admin_user_id = make_user(whatsapp_number="972500000001", is_admin=True)
    non_admin_user_id = make_user(whatsapp_number="972500000002", is_admin=False)

    admin_row = get_user_by_whatsapp_number("972500000001")
    non_admin_row = get_user_by_whatsapp_number("972500000002")
    assert not isinstance(admin_row, dict)  # confirms this is really exercising the sqlite3.Row path

    admin_tool = get_tool("get_infra_status")
    assert admin_tool in tools_for(admin_row)  # must not raise, and must include the admin tool
    assert admin_tool not in tools_for(non_admin_row)  # must not raise, and must exclude it

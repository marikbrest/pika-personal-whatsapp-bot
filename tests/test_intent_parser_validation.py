"""
_validate_result() is the last line of defence between "whatever JSON Gemini
felt like returning" and code that indexes into it with result["reminder"]
etc. It's a pure function (no network, no DB), so this is the cheapest,
highest-value place to lock in every one of its guard clauses.

The shared shape across almost every case: a well-formed envelope for a
given intent is accepted as-is; a missing/empty required field, or an
action outside the fixed enum, always degrades to {"intent": "unclear"} -
never a KeyError three layers deeper in a handler.
"""
import pytest

from src.intent_parser import FALLBACK_REPLY, _validate_result


def test_none_result_becomes_unclear():
    assert _validate_result(None) == {"intent": "unclear", "reply": FALLBACK_REPLY}


def test_unknown_intent_name_becomes_unclear():
    assert _validate_result({"intent": "delete_all_data", "reply": "x"})["intent"] == "unclear"


def test_missing_intent_key_becomes_unclear():
    assert _validate_result({"reply": "x"})["intent"] == "unclear"


@pytest.mark.parametrize(
    "payload",
    [
        {"intent": "reminder", "reminder": {"content": "x"}, "reply": "r"},  # missing schedule fields
        {"intent": "reminder", "reply": "r"},  # missing the whole sub-object
    ],
)
def test_reminder_requires_content_and_schedule(payload):
    assert _validate_result(payload)["intent"] == "unclear"


def test_reminder_with_all_required_fields_passes_through():
    payload = {
        "intent": "reminder",
        "reminder": {"content": "x", "schedule_type": "once", "schedule_time": "2026-01-01T09:00:00"},
        "reply": "r",
    }
    assert _validate_result(payload) is payload


@pytest.mark.parametrize(
    "calendar",
    [
        {"action": "query"},  # missing start/end
        {"action": "create", "start": "s", "end": "e"},  # missing summary
        {"action": "delete"},  # not a valid action at all
        {"action": "update", "start": "2026-01-01T09:00:00"},  # missing match
        {"action": "update", "match": "the meeting"},  # nothing actually asked to change
    ],
)
def test_calendar_action_specific_requirements(calendar):
    result = _validate_result({"intent": "calendar", "calendar": calendar, "reply": "r"})
    assert result["intent"] == "unclear"


@pytest.mark.parametrize(
    "calendar",
    [
        {"action": "update", "match": "the meeting", "start": "2026-01-01T09:00:00"},  # move it
        {"action": "update", "match": "the meeting", "summary": "New title"},  # rename it
        {"action": "update", "match": "the meeting", "start": "2026-01-01T09:00:00", "end": "2026-01-01T10:00:00"},
    ],
)
def test_calendar_update_with_match_and_a_real_change_passes_through(calendar):
    payload = {"intent": "calendar", "calendar": calendar, "reply": "r"}
    assert _validate_result(payload) is payload


@pytest.mark.parametrize(
    "task_manage,should_pass",
    [
        ({"action": "add", "content": "milk"}, True),
        ({"action": "add", "content": None}, False),  # add with no content
        ({"action": "add"}, False),  # add with content missing entirely
        ({"action": "list"}, True),  # list needs nothing
        ({"action": "clear"}, True),
        ({"action": "done", "match": "milk"}, True),
        ({"action": "done", "match": None}, False),  # done without saying which item - refuse, don't guess
        ({"action": "delete"}, False),  # delete without match at all
        ({"action": "bogus_action"}, False),
    ],
)
def test_task_manage_action_specific_requirements(task_manage, should_pass):
    result = _validate_result({"intent": "task_manage", "task_manage": task_manage, "reply": "r"})
    assert (result["intent"] == "task_manage") is should_pass


def test_market_requires_a_nonempty_symbol():
    assert _validate_result({"intent": "market", "market": {"symbol": ""}, "reply": "r"})["intent"] == "unclear"
    assert _validate_result({"intent": "market", "market": {"symbol": "AAPL"}, "reply": "r"})["intent"] == "market"


def test_email_draft_requires_to_subject_and_body():
    assert _validate_result({
        "intent": "email_draft", "email_draft": {"to": "a@b.com", "subject": "s"}, "reply": "r",
    })["intent"] == "unclear"


def test_email_action_requires_a_valid_action():
    assert _validate_result({
        "intent": "email_action", "email_action": {"action": "delete_forever"}, "reply": "r",
    })["intent"] == "unclear"


@pytest.mark.parametrize(
    "reminder_manage,should_pass",
    [
        ({"action": "list"}, True),
        ({"action": "reschedule", "schedule_type": "once", "schedule_time": "09:00"}, True),
        ({"action": "reschedule"}, False),
        ({"action": "snooze", "snooze_minutes": 15}, True),
        ({"action": "snooze"}, False),
        ({"action": "edit_content", "new_content": "x"}, True),
        ({"action": "edit_content"}, False),
    ],
)
def test_reminder_manage_action_specific_requirements(reminder_manage, should_pass):
    result = _validate_result({"intent": "reminder_manage", "reminder_manage": reminder_manage, "reply": "r"})
    assert (result["intent"] == "reminder_manage") is should_pass


@pytest.mark.parametrize(
    "memory,should_pass",
    [
        ({"action": "list"}, True),
        ({"action": "forget_all"}, True),
        ({"action": "save", "fact_key": "diet", "fact_value": "vegetarian"}, True),
        ({"action": "save", "fact_key": "diet"}, False),  # value missing
        ({"action": "forget", "fact_key": "diet"}, True),
        ({"action": "forget"}, False),  # no key to forget
    ],
)
def test_memory_action_specific_requirements(memory, should_pass):
    result = _validate_result({"intent": "memory", "memory": memory, "reply": "r"})
    assert (result["intent"] == "memory") is should_pass


@pytest.mark.parametrize(
    "user_manage,should_pass",
    [
        ({"action": "list"}, True),
        ({"action": "add", "whatsapp_number": "972500000001"}, True),
        ({"action": "add"}, False),  # granting bot access with no number is meaningless - refuse
        ({"action": "disable"}, False),
    ],
)
def test_user_manage_action_specific_requirements(user_manage, should_pass):
    result = _validate_result({"intent": "user_manage", "user_manage": user_manage, "reply": "r"})
    assert (result["intent"] == "user_manage") is should_pass


@pytest.mark.parametrize(
    "saved_link,should_pass",
    [
        ({"action": "list"}, True),
        ({"action": "save", "url": "https://x.com"}, True),
        ({"action": "save"}, False),  # nothing to save
        ({"action": "forget", "match": "x"}, True),
        ({"action": "forget"}, False),
    ],
)
def test_saved_link_action_specific_requirements(saved_link, should_pass):
    result = _validate_result({"intent": "saved_link", "saved_link": saved_link, "reply": "r"})
    assert (result["intent"] == "saved_link") is should_pass


def test_semantic_search_requires_a_query():
    assert _validate_result({
        "intent": "semantic_search", "semantic_search": {"query": ""}, "reply": "r",
    })["intent"] == "unclear"


@pytest.mark.parametrize(
    "drive,should_pass",
    [
        ({"action": "search", "query": "invoice"}, True),
        ({"action": "search", "query": ""}, False),  # nothing to search for
        ({"action": "search"}, False),
        ({"action": "save_note", "filename": "note.txt", "content": "x"}, True),
        ({"action": "save_note", "filename": "note.txt"}, False),  # content missing
        ({"action": "save_note", "content": "x"}, False),  # filename missing
        ({"action": "delete_everything"}, False),
    ],
)
def test_drive_action_specific_requirements(drive, should_pass):
    result = _validate_result({"intent": "drive", "drive": drive, "reply": "r"})
    assert (result["intent"] == "drive") is should_pass


def test_web_search_requires_a_nonempty_query():
    assert _validate_result({
        "intent": "web_search", "web_search": {"query": ""}, "reply": "r",
    })["intent"] == "unclear"
    assert _validate_result({
        "intent": "web_search", "web_search": {"query": "who won yesterday"}, "reply": "r",
    })["intent"] == "web_search"


@pytest.mark.parametrize(
    "email_analyze,should_pass",
    [
        ({"action": "summarize", "query": "dani project"}, True),
        ({"action": "summarize", "query": ""}, False),
        ({"action": "summarize"}, False),
        ({"action": "unanswered"}, True),  # needs no query
        ({"action": "unanswered", "query": None}, True),
        ({"action": "archive_everything"}, False),
    ],
)
def test_email_analyze_action_specific_requirements(email_analyze, should_pass):
    result = _validate_result({"intent": "email_analyze", "email_analyze": email_analyze, "reply": "r"})
    assert (result["intent"] == "email_analyze") is should_pass


@pytest.mark.parametrize(
    "watch_manage,should_pass",
    [
        ({"action": "add", "watch_type": "email_reply", "query": "dani project"}, True),
        ({"action": "add", "watch_type": "email_reply"}, False),  # query missing
        ({"action": "add", "watch_type": "web_page", "url": "https://x.com"}, True),
        ({"action": "add", "watch_type": "web_page"}, False),  # url missing
        ({"action": "add", "watch_type": "carrier_pigeon", "url": "https://x.com"}, False),
        ({"action": "add"}, False),  # watch_type missing entirely
        ({"action": "list"}, True),
        ({"action": "cancel", "match": "the docs page"}, True),
        ({"action": "cancel"}, False),  # nothing to match against
        ({"action": "delete_everything"}, False),
    ],
)
def test_watch_manage_action_specific_requirements(watch_manage, should_pass):
    result = _validate_result({"intent": "watch_manage", "watch_manage": watch_manage, "reply": "r"})
    assert (result["intent"] == "watch_manage") is should_pass


@pytest.mark.parametrize(
    "intent",
    ["chat", "connect_google", "morning_brief", "zabbix_status", "package_status", "usage_status"],
)
def test_intents_with_no_required_sub_object_pass_through_unchanged(intent):
    """These intents carry no mandatory nested fields - the placeholder reply
    itself, and any optional sub-object, is enough."""
    payload = {"intent": intent, "reply": "r"}
    assert _validate_result(payload) is payload

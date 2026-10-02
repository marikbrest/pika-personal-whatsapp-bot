"""
src.integrations.zabbix - ZABBIX_API_TOKEN is forced blank in conftest.py
(see the incident note there), so get_active_problems() calling the real,
unmocked _call() exercises the genuine "not configured" path. Everything else here
patches _call directly (the actual network boundary) so the real filtering/
sorting/formatting logic in get_active_problems runs unmocked - including
the regression for the 2026-09-13 disabled-trigger bug (commit cf26ed0).
"""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src.integrations.zabbix import ZabbixNotConfiguredError, format_problems_for_reply, get_active_problems


def _problem(objectid, severity, name="בעיה", clock=1757700000, tags=None):
    return {"name": name, "severity": severity, "clock": clock, "objectid": objectid, "tags": tags or []}


def _trigger(triggerid, host, status="0"):
    return {"triggerid": triggerid, "status": status, "hosts": [{"host": host}]}


def test_raises_not_configured_when_token_missing():
    """Real code path, no mocking - conftest.py never sets ZABBIX_API_TOKEN."""
    with pytest.raises(ZabbixNotConfiguredError):
        get_active_problems()


def test_filters_out_scope_notice_tagged_problems():
    problems = [
        _problem("1", "3", name="Real problem"),
        _problem("2", "2", name="Noise", tags=[{"tag": "scope", "value": "notice"}]),
    ]

    def fake_call(method, params):
        if method == "problem.get":
            return problems
        return [_trigger("1", "windows-home-server")]

    with patch("src.integrations.zabbix._call", side_effect=fake_call):
        result = get_active_problems()

    assert len(result) == 1
    assert result[0]["description"] == "Real problem"


def test_excludes_problems_whose_trigger_is_disabled():
    """Regression for commit cf26ed0 - a trigger switched off must not keep
    reporting its old, un-clearable problem forever."""
    problems = [
        _problem("1", "3", name="Still relevant"),
        _problem("2", "3", name="Trigger was disabled - stale"),
    ]

    def fake_call(method, params):
        if method == "problem.get":
            return problems
        return [
            _trigger("1", "host-a", status="0"),  # enabled
            _trigger("2", "host-b", status="1"),  # disabled
        ]

    with patch("src.integrations.zabbix._call", side_effect=fake_call):
        result = get_active_problems()

    assert [p["description"] for p in result] == ["Still relevant"]


def test_sorts_most_severe_first():
    problems = [_problem("1", "2"), _problem("2", "5"), _problem("3", "3")]

    def fake_call(method, params):
        if method == "problem.get":
            return problems
        return [_trigger(str(i), "h") for i in (1, 2, 3)]

    with patch("src.integrations.zabbix._call", side_effect=fake_call):
        result = get_active_problems()

    assert [p["severity"] for p in result] == ["5", "3", "2"]


def test_no_problems_returns_empty_list_without_a_second_call():
    with patch("src.integrations.zabbix._call", side_effect=[[]]) as mock_call:
        result = get_active_problems()
    assert result == []
    mock_call.assert_called_once()  # trigger.get is skipped entirely - nothing to look up


def test_format_problems_for_reply_when_all_clear():
    assert format_problems_for_reply([], "Asia/Jerusalem") == "✅ הכל תקין, אין בעיות פתוחות בזאביקס כרגע."


def test_format_problems_for_reply_includes_host_description_and_local_time():
    problems = [{
        "host": "windows-home-server",
        "description": "Disk almost full",
        "severity": "4",
        "since": datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc),
    }]
    text = format_problems_for_reply(problems, "Asia/Jerusalem")
    assert "windows-home-server" in text
    assert "Disk almost full" in text
    assert "🔴" in text  # severity 4 emoji
    assert "13/09" in text  # 10:00 UTC on 2026-09-13 is within the day either way DST falls

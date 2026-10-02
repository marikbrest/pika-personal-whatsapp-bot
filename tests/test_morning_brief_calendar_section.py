"""
morning_brief._calendar_section / _calendar_section_or_none (2026-09-16
refactor): _calendar_section now RAISES NotConnectedError/
GoogleAuthExpiredError instead of swallowing them, so
scheduler.check_and_send_daily_meetings_summaries can react to a "calendar
sync failed" situation by sending the reconnect link. build_morning_brief
(the on-demand brief) still wants the old "just omit this section"
behavior, via _calendar_section_or_none - these tests lock in both
contracts so a future change can't silently break either caller.
"""
from unittest.mock import patch

from src.integrations.google_oauth import GoogleAuthExpiredError, NotConnectedError
from src.morning_brief import _calendar_section, _calendar_section_or_none, build_morning_brief


def test_calendar_section_raises_on_not_connected():
    with patch("src.morning_brief.list_events", side_effect=NotConnectedError()):
        try:
            _calendar_section(1, "Asia/Jerusalem")
            assert False, "expected NotConnectedError to propagate"
        except NotConnectedError:
            pass


def test_calendar_section_raises_on_expired_token():
    with patch("src.morning_brief.list_events", side_effect=GoogleAuthExpiredError()):
        try:
            _calendar_section(1, "Asia/Jerusalem")
            assert False, "expected GoogleAuthExpiredError to propagate"
        except GoogleAuthExpiredError:
            pass


def test_calendar_section_still_swallows_a_non_auth_failure():
    with patch("src.morning_brief.list_events", side_effect=RuntimeError("transient")):
        assert _calendar_section(1, "Asia/Jerusalem") is None


def test_calendar_section_or_none_hides_not_connected():
    with patch("src.morning_brief.list_events", side_effect=NotConnectedError()):
        assert _calendar_section_or_none(1, "Asia/Jerusalem") is None


def test_calendar_section_or_none_hides_expired_token():
    with patch("src.morning_brief.list_events", side_effect=GoogleAuthExpiredError()):
        assert _calendar_section_or_none(1, "Asia/Jerusalem") is None


def test_build_morning_brief_omits_calendar_section_when_not_connected_regression():
    """The on-demand brief must not start throwing or breaking just because
    _calendar_section's internals changed to raise - it should read exactly
    as it did before this refactor (calendar section simply absent)."""
    with patch("src.morning_brief.list_events", side_effect=NotConnectedError()), \
         patch("src.morning_brief.get_current_weather", side_effect=Exception("no weather in test")), \
         patch("src.morning_brief.list_recent_emails", side_effect=NotConnectedError()):
        reply = build_morning_brief(user_id=1, timezone_name="Asia/Jerusalem", display_name="Yossi")

    assert "לא הצלחתי להביא מידע" in reply
    assert "בוקר טוב" in reply

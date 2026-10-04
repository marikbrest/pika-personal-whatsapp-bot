"""LOCALE=en (2026-10-04): the messages the code itself composes come out in English, with no Hebrew left in them."""
import re
from unittest.mock import patch

import pytest

from src import config, i18n
from src.db.models import get_calendar_snapshot, set_proactive_enabled

HEBREW = re.compile("[א-ת]")


@pytest.fixture
def english(monkeypatch):
    monkeypatch.setattr(config, "LOCALE", "en")


def test_english_catalog_contains_no_hebrew_letters():
    for key, text in i18n.catalog("en").items():
        assert not HEBREW.search(text), f"{key} still has Hebrew: {text[:60]!r}"


def test_schedule_descriptions_are_english(english):
    from src.scheduler import format_schedule_description

    assert format_schedule_description("daily", "08:00", None, "Asia/Jerusalem") == "Every day at 08:00"
    assert format_schedule_description("weekly", "09:30", "mon,wed", "Asia/Jerusalem") == "Every Monday, Wednesday at 09:30"
    once = format_schedule_description("once", "2026-10-05T14:00:00", None, "Asia/Jerusalem")
    assert once == "One-time, 05/10 at 14:00"


def test_hebrew_schedule_descriptions_are_unchanged():
    from src.scheduler import format_schedule_description

    assert format_schedule_description("daily", "08:00", None, "Asia/Jerusalem") == "כל יום ב-08:00"
    assert format_schedule_description("weekly", "09:30", "mon,wed", "Asia/Jerusalem") == "כל שני, רביעי ב-09:30"


def test_package_status_sentinels_stay_hebrew_in_the_db_but_display_localized(english):
    from src.scheduler import _ABANDONED_PACKAGE_STATUS, _UNKNOWN_PACKAGE_STATUS, package_status_label

    assert (_ABANDONED_PACKAGE_STATUS, _UNKNOWN_PACKAGE_STATUS) == ("לא נמצא", "לא ידוע")  # stored values never change
    assert package_status_label(_UNKNOWN_PACKAGE_STATUS) == "Unknown"
    assert package_status_label(_ABANDONED_PACKAGE_STATUS) == "Not found"
    assert package_status_label("in_transit") == "in_transit"


def test_calendar_monitor_alert_is_english(db_path, make_user, english):
    from src.scheduler import check_and_monitor_calendar_changes

    make_user(whatsapp_number="972500000001", display_name="Dana", is_admin=True)
    set_proactive_enabled(1, True)

    def event(start):
        return {"id": "e1", "summary": "Dentist", "start": start, "end": "2026-12-01T11:00:00+02:00", "location": None}

    with patch("src.integrations.google_calendar.list_events", return_value=[event("2026-12-01T10:00:00+02:00")]), \
         patch("src.integrations.whatsapp.send_text_or_template"):
        check_and_monitor_calendar_changes()
    with patch("src.integrations.google_calendar.list_events", return_value=[event("2026-12-01T10:30:00+02:00")]), \
         patch("src.proactive.assess_situation", return_value=None), \
         patch("src.integrations.whatsapp.send_text_or_template", return_value=True) as send:
        check_and_monitor_calendar_changes()

    body = send.call_args.kwargs["body"]
    assert "An event was moved: Dentist" in body
    assert not HEBREW.search(body)
    assert get_calendar_snapshot(1)["e1"]["start"] == "2026-12-01T10:30:00+02:00"


def test_assessment_prompt_is_english_and_asks_for_english(db_path, make_user, english):
    from src.proactive import assess_situation

    make_user(whatsapp_number="972500000001", display_name="Dana")
    user = {"id": 1, "timezone": "Asia/Jerusalem"}
    with patch("src.integrations.gemini.call_gemini_json", return_value={"interrupt": False}) as call, \
         patch("src.proactive.get_proactive_settings", return_value=None), \
         patch("src.proactive.count_todays_proactive_notifications", return_value=0):
        assess_situation(user, "A meeting starts soon", "calendar_moved", calendar_events=[])

    prompt = call.call_args.args[0]
    assert "You decide whether it is worth interrupting the user" in prompt
    assert "No upcoming events on the calendar" in prompt
    assert "in English" in prompt
    assert not HEBREW.search(prompt)


def test_morning_brief_sections_are_english(english):
    from src.morning_brief import build_morning_brief

    with patch("src.morning_brief._weather_section", return_value=None), \
         patch("src.morning_brief._calendar_section_or_none", return_value="📅 Today on your calendar:\n- x"), \
         patch("src.morning_brief._email_section", return_value=None):
        brief = build_morning_brief(1, "Asia/Jerusalem", display_name="Dana")
    assert brief.startswith("☀️ Good morning, Dana!")

    with patch("src.morning_brief._weather_section", return_value=None), \
         patch("src.morning_brief._calendar_section_or_none", return_value=None), \
         patch("src.morning_brief._email_section", return_value=None):
        empty = build_morning_brief(1, "Asia/Jerusalem")
    assert "couldn't get any information" in empty and not HEBREW.search(empty)


def test_weather_text_is_english(english):
    from src.integrations.weather import _describe_code, format_weather_for_reply

    assert _describe_code(63) == "Rain"
    assert _describe_code(999) == "Weather code 999"
    text = format_weather_for_reply({"location": "London", "description": "Rain", "temperature": 11.2, "feels_like": 9.4, "wind_speed": 20})
    assert text == "🌤️ Weather in London:\nRain, 11°C (feels like 9°C)\n💨 Wind: 20 km/h"


def test_oauth_error_page_is_left_to_right_english(english):
    from src.oauth_handler import _error_page, _success_page

    page = _error_page("The link has expired.")
    assert 'dir="ltr"' in page and "Connection failed" in page and not HEBREW.search(page)
    assert "Connected!" in _success_page()


def test_oauth_pages_stay_right_to_left_hebrew_by_default():
    from src.oauth_handler import _success_page

    assert 'dir="rtl"' in _success_page() and "החיבור הצליח" in _success_page()


def test_privacy_and_capabilities_are_english(english):
    from src.webhook_handler import _handle_explain_capabilities, _handle_explain_privacy

    assert _handle_explain_privacy().startswith("I take your privacy seriously")
    assert not HEBREW.search(_handle_explain_privacy())
    reply = _handle_explain_capabilities({"id": 1, "is_admin": False})
    assert reply.startswith("Here's what I can do:") and not HEBREW.search(reply)


def test_formatters_are_english(english):
    from src.integrations.gmail import format_emails_for_reply, format_unanswered_for_reply
    from src.integrations.google_calendar import format_events_for_reply

    assert format_emails_for_reply([]) == "No matching emails."
    assert format_unanswered_for_reply([]).startswith("No emails are waiting")
    assert format_events_for_reply([], "Asia/Jerusalem") == "No events in this time range."
    assert "All day" in format_events_for_reply([{"start": "2026-10-05", "summary": "Holiday", "location": None}], "Asia/Jerusalem")


def test_watch_labels_follow_the_locale(monkeypatch):
    from src.integrations.watchers import NOTIFY_MESSAGES, WATCH_TYPE_LABELS

    assert WATCH_TYPE_LABELS.get("web_page") == "🌐 עמוד אינטרנט"
    monkeypatch.setattr(config, "LOCALE", "en")
    assert WATCH_TYPE_LABELS.get("web_page") == "🌐 Web page"
    assert WATCH_TYPE_LABELS.get("nope", "x") == "x"
    assert "has changed" in NOTIFY_MESSAGES["web_page"]("https://example.com")


def test_model_facing_answer_language_follows_the_locale(monkeypatch):
    monkeypatch.setattr(config, "LOCALE", "en")
    assert i18n.answer_language_line() == "Write the text addressed to the user in English."
    assert i18n.language_name_english() == "English"
    monkeypatch.setattr(config, "LOCALE", "he")
    assert i18n.language_name_english() == "Hebrew"

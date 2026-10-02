"""
check_and_send_meeting_prebriefs (2026-09-27, Gatekeeper stage 3) - the
third collector. Mocks list_events and proactive.assess_situation (its own
correctness covered elsewhere - returning None here exercises the plain
fallback path, avoiding a real Gemini call). Real DB fixtures for
proactive_settings/meeting_prebrief_sent state.
"""
import pytest
from unittest.mock import patch

from src.db.models import (
    get_connection,
    set_proactive_enabled,
    set_proactive_meeting_lead_time,
)
from src.scheduler import check_and_send_meeting_prebriefs

# Proactive delivery is quiet-hours-gated; never depend on the real time of day.
pytestmark = pytest.mark.usefixtures("daytime_clock")


def _event(event_id, summary, start, end, location=None):
    return {"id": event_id, "summary": summary, "start": start, "end": end, "location": location}


def _is_prebriefed(user_id, event_id):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM meeting_prebrief_sent WHERE user_id = ? AND event_id = ?", (user_id, event_id)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def test_does_nothing_for_a_user_who_never_opted_in(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    with patch("src.integrations.google_calendar.list_events") as mock_list, \
         patch("src.integrations.whatsapp.send_text_or_template") as mock_send:
        check_and_send_meeting_prebriefs()

    mock_list.assert_not_called()
    mock_send.assert_not_called()


def test_sends_a_prebrief_for_an_event_inside_the_lead_window(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    set_proactive_enabled(1, True)
    set_proactive_meeting_lead_time(1, 15)
    events = [_event("e1", "פגישה עם הצוות", "2026-01-01T10:00:00+00:00", "2026-01-01T11:00:00+00:00", "זום")]

    with patch("src.integrations.google_calendar.list_events", return_value=events), \
         patch("src.proactive.assess_situation", return_value=None), \
         patch("src.integrations.whatsapp.send_text_or_template", return_value=True) as mock_send:
        check_and_send_meeting_prebriefs()

    mock_send.assert_called_once()
    body = mock_send.call_args.kwargs["body"]
    assert "פגישה עם הצוות" in body
    assert "זום" in body
    assert "15" in body
    assert _is_prebriefed(1, "e1")


def test_uses_the_users_own_configured_lead_time(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    set_proactive_enabled(1, True)
    set_proactive_meeting_lead_time(1, 5)

    with patch("src.integrations.google_calendar.list_events", return_value=[]) as mock_list, \
         patch("src.integrations.whatsapp.send_text_or_template"):
        check_and_send_meeting_prebriefs()

    # window end passed to list_events must reflect the 5-minute lead time, not the default 15
    call_kwargs = mock_list.call_args
    time_min, time_max = call_kwargs.args[1], call_kwargs.args[2]
    assert (time_max - time_min).total_seconds() == 5 * 60


def test_does_not_resend_a_prebrief_already_marked_sent(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    set_proactive_enabled(1, True)
    events = [_event("e1", "פגישה", "2026-01-01T10:00:00+00:00", "2026-01-01T11:00:00+00:00")]

    with patch("src.integrations.google_calendar.list_events", return_value=events), \
         patch("src.proactive.assess_situation", return_value=None), \
         patch("src.integrations.whatsapp.send_text_or_template", return_value=True) as mock_send:
        check_and_send_meeting_prebriefs()
        check_and_send_meeting_prebriefs()

    mock_send.assert_called_once()


def test_marks_sent_even_when_the_assessment_declines_to_interrupt(db_path, make_user):
    """A "no, not worth it" judgment call must not cause the same event to
    be re-considered every 2-minute poll until it starts."""
    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    set_proactive_enabled(1, True)
    events = [_event("e1", "פגישה", "2026-01-01T10:00:00+00:00", "2026-01-01T11:00:00+00:00")]

    with patch("src.integrations.google_calendar.list_events", return_value=events), \
         patch("src.proactive.assess_situation", return_value={"interrupt": False, "message": ""}), \
         patch("src.integrations.whatsapp.send_text_or_template") as mock_send:
        check_and_send_meeting_prebriefs()

    mock_send.assert_not_called()
    assert _is_prebriefed(1, "e1")


def test_event_with_no_location_is_still_briefed(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    set_proactive_enabled(1, True)
    events = [_event("e1", "פגישה", "2026-01-01T10:00:00+00:00", "2026-01-01T11:00:00+00:00", location=None)]

    with patch("src.integrations.google_calendar.list_events", return_value=events), \
         patch("src.proactive.assess_situation", return_value=None), \
         patch("src.integrations.whatsapp.send_text_or_template", return_value=True) as mock_send:
        check_and_send_meeting_prebriefs()

    mock_send.assert_called_once()


def test_a_broken_google_connection_for_one_user_does_not_block_others(db_path, make_user):
    from src.integrations.google_oauth import NotConnectedError

    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    make_user(whatsapp_number="972500000002", display_name="רונית", is_admin=False)
    set_proactive_enabled(1, True)
    set_proactive_enabled(2, True)
    events = [_event("e1", "פגישה", "2026-01-01T10:00:00+00:00", "2026-01-01T11:00:00+00:00")]

    def _list_events(user_id, *a, **kw):
        if user_id == 1:
            raise NotConnectedError("not connected")
        return events

    with patch("src.integrations.google_calendar.list_events", side_effect=_list_events), \
         patch("src.proactive.assess_situation", return_value=None), \
         patch("src.integrations.whatsapp.send_text_or_template"):
        check_and_send_meeting_prebriefs()  # must not raise

    assert _is_prebriefed(2, "e1")

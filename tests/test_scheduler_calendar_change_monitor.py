"""
check_and_monitor_calendar_changes (2026-09-27) - the Context-Aware
Gatekeeper's first real collector. Mocks list_events (the Google Calendar
integration's own correctness is proven elsewhere), send_text_message, and
proactive.assess_situation (its own correctness covered by
test_proactive_situation_assessment.py) - returning None here means
assess_and_deliver falls back to the collector's own plain factual
message, the same path a real assessment-call failure takes, so these
tests exercise this collector's own message text without making a real
(and non-deterministic) Gemini call. Uses real DB fixtures for
settings/snapshot state.
"""
import pytest
from unittest.mock import patch

from src.db.models import (
    get_calendar_snapshot,
    set_proactive_enabled,
)
from src.scheduler import check_and_monitor_calendar_changes

# Proactive delivery is quiet-hours-gated; never depend on the real time of day.
pytestmark = pytest.mark.usefixtures("daytime_clock")


def _event(event_id, summary, start, end):
    return {"id": event_id, "summary": summary, "start": start, "end": end, "location": None}


def test_does_nothing_for_a_user_who_never_opted_in(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    with patch("src.integrations.google_calendar.list_events") as mock_list, \
         patch("src.integrations.whatsapp.send_text_or_template") as mock_send:
        check_and_monitor_calendar_changes()

    mock_list.assert_not_called()
    mock_send.assert_not_called()


def test_first_run_seeds_the_snapshot_without_notifying(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    set_proactive_enabled(1, True)
    events = [_event("e1", "פגישה", "2026-01-01T10:00:00+02:00", "2026-01-01T11:00:00+02:00")]

    with patch("src.integrations.google_calendar.list_events", return_value=events), \
         patch("src.integrations.whatsapp.send_text_or_template") as mock_send:
        check_and_monitor_calendar_changes()

    mock_send.assert_not_called()  # first run - no false "new event" flood
    snapshot = get_calendar_snapshot(1)
    assert "e1" in snapshot
    assert snapshot["e1"]["summary"] == "פגישה"


def test_a_genuinely_new_event_after_the_first_run_is_reported(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    set_proactive_enabled(1, True)
    first = [_event("e1", "פגישה", "2026-01-01T10:00:00+02:00", "2026-01-01T11:00:00+02:00")]
    second = first + [_event("e2", "פגישה חדשה", "2026-01-01T14:00:00+02:00", "2026-01-01T15:00:00+02:00")]

    with patch("src.integrations.google_calendar.list_events", return_value=first), \
         patch("src.integrations.whatsapp.send_text_or_template"):
        check_and_monitor_calendar_changes()  # seeds silently

    with patch("src.integrations.google_calendar.list_events", return_value=second), \
         patch("src.proactive.assess_situation", return_value=None), \
         patch("src.integrations.whatsapp.send_text_or_template", return_value=True) as mock_send:
        check_and_monitor_calendar_changes()

    mock_send.assert_called_once()
    assert "פגישה חדשה" in mock_send.call_args.kwargs["body"]
    assert "e2" in get_calendar_snapshot(1)


def test_a_moved_event_is_reported(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    set_proactive_enabled(1, True)
    original = [_event("e1", "פגישה", "2026-01-01T10:00:00+02:00", "2026-01-01T11:00:00+02:00")]
    moved = [_event("e1", "פגישה", "2026-01-01T12:00:00+02:00", "2026-01-01T13:00:00+02:00")]

    with patch("src.integrations.google_calendar.list_events", return_value=original), \
         patch("src.integrations.whatsapp.send_text_or_template"):
        check_and_monitor_calendar_changes()

    with patch("src.integrations.google_calendar.list_events", return_value=moved), \
         patch("src.proactive.assess_situation", return_value=None), \
         patch("src.integrations.whatsapp.send_text_or_template", return_value=True) as mock_send:
        check_and_monitor_calendar_changes()

    mock_send.assert_called_once()
    assert "הוזז" in mock_send.call_args.kwargs["body"]
    assert get_calendar_snapshot(1)["e1"]["start"] == "2026-01-01T12:00:00+02:00"


def test_a_cancelled_event_is_reported_and_removed_from_the_snapshot(db_path, make_user):
    """Uses a start time in the future (relative to the real clock), not a
    fixed past date - a genuinely cancelled event must still be reported
    even though it hasn't happened yet, which is exactly what distinguishes
    it from one that simply aged out of the lookahead window (see the
    dedicated test for that case below)."""
    from datetime import datetime, timedelta, timezone

    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    set_proactive_enabled(1, True)
    future_start = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    future_end = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    original = [_event("e1", "פגישה", future_start, future_end)]

    with patch("src.integrations.google_calendar.list_events", return_value=original), \
         patch("src.integrations.whatsapp.send_text_or_template"):
        check_and_monitor_calendar_changes()

    with patch("src.integrations.google_calendar.list_events", return_value=[]), \
         patch("src.proactive.assess_situation", return_value=None), \
         patch("src.integrations.whatsapp.send_text_or_template", return_value=True) as mock_send:
        check_and_monitor_calendar_changes()

    mock_send.assert_called_once()
    assert "בוטל" in mock_send.call_args.kwargs["body"]
    assert "e1" not in get_calendar_snapshot(1)


def test_an_unchanged_event_is_not_reported(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    set_proactive_enabled(1, True)
    events = [_event("e1", "פגישה", "2026-01-01T10:00:00+02:00", "2026-01-01T11:00:00+02:00")]

    with patch("src.integrations.google_calendar.list_events", return_value=events), \
         patch("src.integrations.whatsapp.send_text_or_template"):
        check_and_monitor_calendar_changes()

    with patch("src.integrations.google_calendar.list_events", return_value=events), \
         patch("src.integrations.whatsapp.send_text_or_template") as mock_send:
        check_and_monitor_calendar_changes()

    mock_send.assert_not_called()


def test_the_llm_assessment_can_suppress_a_real_change(db_path, make_user):
    """The situation-assessment layer's whole point: a detected change is
    not automatically sent - if the judgment call says it's not worth
    interrupting for, nothing goes out even though something real changed."""
    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    set_proactive_enabled(1, True)
    original = [_event("e1", "פגישה", "2026-01-01T10:00:00+02:00", "2026-01-01T11:00:00+02:00")]
    moved = [_event("e1", "פגישה", "2026-01-01T10:05:00+02:00", "2026-01-01T11:05:00+02:00")]

    with patch("src.integrations.google_calendar.list_events", return_value=original), \
         patch("src.integrations.whatsapp.send_text_or_template"):
        check_and_monitor_calendar_changes()

    with patch("src.integrations.google_calendar.list_events", return_value=moved), \
         patch("src.proactive.assess_situation", return_value={"interrupt": False, "message": ""}), \
         patch("src.integrations.whatsapp.send_text_or_template") as mock_send:
        check_and_monitor_calendar_changes()

    mock_send.assert_not_called()  # a 5-minute shift judged not worth interrupting for


def test_an_event_that_simply_already_happened_is_not_reported_as_cancelled(db_path, make_user):
    """Real bug found live 2026-09-27: the 24h lookahead is a sliding
    window - an event whose start has simply passed naturally stops being
    returned by list_events, exactly like a genuinely cancelled one would.
    Without a start-time check, EVERY event would eventually get reported
    as "cancelled" purely for having already happened."""
    from datetime import datetime, timedelta, timezone

    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    set_proactive_enabled(1, True)
    # A start time safely in the past relative to any real "now".
    past_start = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    past_end = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    original = [_event("e1", "פגישה שכבר קרתה", past_start, past_end)]

    with patch("src.integrations.google_calendar.list_events", return_value=original), \
         patch("src.integrations.whatsapp.send_text_or_template"):
        check_and_monitor_calendar_changes()  # seeds

    # Next poll: the event is gone from live results simply because its
    # own start time is now outside the [now, now+24h) window.
    with patch("src.integrations.google_calendar.list_events", return_value=[]), \
         patch("src.proactive.assess_situation", return_value=None), \
         patch("src.integrations.whatsapp.send_text_or_template") as mock_send:
        check_and_monitor_calendar_changes()

    mock_send.assert_not_called()  # aged out, not cancelled - no false alert
    assert "e1" not in get_calendar_snapshot(1)  # still pruned from the snapshot


def test_a_broken_google_connection_for_one_user_does_not_block_others(db_path, make_user):
    from src.integrations.google_oauth import NotConnectedError

    make_user(whatsapp_number="972500000001", display_name="יוסי", is_admin=True)
    make_user(whatsapp_number="972500000002", display_name="רונית", is_admin=False)
    set_proactive_enabled(1, True)
    set_proactive_enabled(2, True)

    events = [_event("e1", "פגישה", "2026-01-01T10:00:00+02:00", "2026-01-01T11:00:00+02:00")]

    def _list_events(user_id, *a, **kw):
        if user_id == 1:
            raise NotConnectedError("not connected")
        return events

    with patch("src.integrations.google_calendar.list_events", side_effect=_list_events), \
         patch("src.integrations.whatsapp.send_text_or_template"):
        check_and_monitor_calendar_changes()  # must not raise

    assert "e1" in get_calendar_snapshot(2)  # user 2 still processed normally

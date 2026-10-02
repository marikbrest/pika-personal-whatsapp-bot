"""
Calendar write actions (2026-09-14): find_event_by_match, update_event,
check_conflicts - the "move/update an existing event" and "detect overlaps"
extension to the calendar intent. Same mocking pattern as the other Google
integration tests: patch _get_calendar_service directly.
"""
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from src.integrations.google_calendar import check_conflicts, create_event, find_event_by_match, update_event

TZ = ZoneInfo("Asia/Jerusalem")


def _event(event_id, summary, start, end):
    return {
        "id": event_id,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
        "summary": summary,
    }


def _service_with_events(events):
    service = MagicMock()
    service.events.return_value.list.return_value.execute.return_value = {"items": events}
    return service


def test_find_event_by_match_returns_the_soonest_matching_event():
    events = [
        _event("e1", "Meeting with Dani", "2026-01-01T09:00:00+02:00", "2026-01-01T10:00:00+02:00"),
        _event("e2", "Team standup", "2026-01-02T09:00:00+02:00", "2026-01-02T09:30:00+02:00"),
    ]
    with patch("src.integrations.google_calendar._get_calendar_service", return_value=_service_with_events(events)):
        result = find_event_by_match(user_id=1, match="dani", timezone_name="Asia/Jerusalem")
    assert result["id"] == "e1"


def test_find_event_by_match_is_case_insensitive():
    events = [_event("e1", "Team Standup", "2026-01-01T09:00:00+02:00", "2026-01-01T09:30:00+02:00")]
    with patch("src.integrations.google_calendar._get_calendar_service", return_value=_service_with_events(events)):
        result = find_event_by_match(user_id=1, match="STANDUP", timezone_name="Asia/Jerusalem")
    assert result["id"] == "e1"


def test_find_event_by_match_returns_none_when_nothing_matches():
    events = [_event("e1", "Team Standup", "2026-01-01T09:00:00+02:00", "2026-01-01T09:30:00+02:00")]
    with patch("src.integrations.google_calendar._get_calendar_service", return_value=_service_with_events(events)):
        result = find_event_by_match(user_id=1, match="dentist", timezone_name="Asia/Jerusalem")
    assert result is None


def test_update_event_sends_only_the_fields_given():
    service = MagicMock()
    with patch("src.integrations.google_calendar._get_calendar_service", return_value=service):
        update_event(user_id=1, event_id="e1", timezone_name="Asia/Jerusalem", new_summary="New title")

    kwargs = service.events.return_value.patch.call_args.kwargs
    assert kwargs["eventId"] == "e1"
    assert kwargs["body"] == {"summary": "New title"}  # no start/end - only what was asked to change


def test_update_event_moving_time_sends_start_and_end():
    service = MagicMock()
    start = datetime(2026, 1, 1, 15, 0, tzinfo=TZ)
    end = datetime(2026, 1, 1, 16, 0, tzinfo=TZ)
    with patch("src.integrations.google_calendar._get_calendar_service", return_value=service):
        update_event(user_id=1, event_id="e1", timezone_name="Asia/Jerusalem", new_start=start, new_end=end)

    body = service.events.return_value.patch.call_args.kwargs["body"]
    assert body["start"]["dateTime"] == start.isoformat()
    assert body["end"]["dateTime"] == end.isoformat()
    assert "summary" not in body


def test_check_conflicts_returns_overlapping_events():
    events = [_event("e1", "Existing meeting", "2026-01-01T09:00:00+02:00", "2026-01-01T10:00:00+02:00")]
    start = datetime(2026, 1, 1, 9, 30, tzinfo=TZ)
    end = datetime(2026, 1, 1, 10, 30, tzinfo=TZ)
    with patch("src.integrations.google_calendar._get_calendar_service", return_value=_service_with_events(events)):
        result = check_conflicts(user_id=1, start=start, end=end, timezone_name="Asia/Jerusalem")
    assert len(result) == 1
    assert result[0]["id"] == "e1"


def test_check_conflicts_excludes_the_given_event_id():
    """Rescheduling an event must not report it as conflicting with its own
    current slot."""
    events = [_event("e1", "The event being moved", "2026-01-01T09:00:00+02:00", "2026-01-01T10:00:00+02:00")]
    start = datetime(2026, 1, 1, 9, 0, tzinfo=TZ)
    end = datetime(2026, 1, 1, 10, 0, tzinfo=TZ)
    with patch("src.integrations.google_calendar._get_calendar_service", return_value=_service_with_events(events)):
        result = check_conflicts(user_id=1, start=start, end=end, timezone_name="Asia/Jerusalem", exclude_event_id="e1")
    assert result == []


def _service_capturing_insert():
    service = MagicMock()
    service.events.return_value.insert.return_value.execute.return_value = {"id": "new-event-id"}
    return service


def test_create_event_without_attendees_sends_update_none():
    """2026-09-26: sendUpdates must be 'none' (not 'all') when there are no
    attendees - Google would otherwise have nobody to notify anyway, but
    the parameter itself should still reflect intent correctly."""
    service = _service_capturing_insert()
    start = datetime(2026, 1, 1, 9, 0, tzinfo=TZ)
    end = datetime(2026, 1, 1, 10, 0, tzinfo=TZ)
    with patch("src.integrations.google_calendar._get_calendar_service", return_value=service):
        event_id = create_event(1, "Meeting", start, end, "Asia/Jerusalem")

    assert event_id == "new-event-id"
    kwargs = service.events.return_value.insert.call_args.kwargs
    assert kwargs["sendUpdates"] == "none"
    assert "attendees" not in kwargs["body"]


def test_create_event_with_attendees_sends_a_real_invite():
    """2026-09-26 bug fix: a real Google Calendar invite (attendees +
    sendUpdates='all'), not a second independently-created event - found
    live that the latter creates genuine duplicate clutter when the two
    users' calendars already overlap via a manually-sent invite."""
    service = _service_capturing_insert()
    start = datetime(2026, 1, 1, 9, 0, tzinfo=TZ)
    end = datetime(2026, 1, 1, 10, 0, tzinfo=TZ)
    with patch("src.integrations.google_calendar._get_calendar_service", return_value=service):
        create_event(1, "Meeting", start, end, "Asia/Jerusalem", attendee_emails=["yossi@example.com"])

    kwargs = service.events.return_value.insert.call_args.kwargs
    assert kwargs["sendUpdates"] == "all"
    assert kwargs["body"]["attendees"] == [{"email": "yossi@example.com"}]

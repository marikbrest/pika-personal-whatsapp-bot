"""
_handle_calendar's attendee_names handling - found live: one parent
scheduled a meeting naming the other parent as an attendee, and that
other parent's own Google Calendar stayed completely empty - the event
only ever went onto the requester's own calendar, with no indication to
anyone else that anything had happened.

The FIRST fix attempt independently created a SECOND, separate event on
the other person's own calendar - discovered live to be wrong (the two
parents' real Google Calendars already overlap in ways that made an
independently-created "duplicate" event genuinely conflict with a real
Calendar invite the other parent had sent manually). The corrected
version adds the other family member as a REAL Google Calendar invite
(attendees + sendUpdates) on the SAME event instead - see
_resolve_family_member_attendee_emails and create_event's attendee_emails
parameter.
"""
from unittest.mock import patch

from src.db.models import get_user_by_whatsapp_number
from src.integrations.google_oauth import GoogleAuthExpiredError, NotConnectedError
from src.webhook_handler import _handle_calendar

START = "2026-09-28T19:00:00"
END = "2026-09-28T20:00:00"


def _user(whatsapp_number):
    return get_user_by_whatsapp_number(whatsapp_number)


def test_create_event_invites_a_named_family_member_by_email(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי")
    make_user(whatsapp_number="972500000002", display_name="רונית")
    ronit = _user("972500000002")

    with patch("src.webhook_handler.check_conflicts", return_value=[]), \
         patch("src.integrations.gmail.get_own_email_address", return_value="yossi@example.com"), \
         patch("src.webhook_handler.create_event") as mock_create:
        reply = _handle_calendar(
            ronit, {"action": "create", "start": START, "end": END, "summary": "פגישה",
                     "attendee_names": ["יוסי"]},
        )

    mock_create.assert_called_once()  # ONE event, not a second independent copy
    assert mock_create.call_args.args[0] == ronit["id"]  # still created on the requester's own calendar
    assert mock_create.call_args.kwargs["attendee_emails"] == ["yossi@example.com"]
    assert "הזמנה" in reply


def test_create_event_recognizes_a_known_nickname_alias(db_path, make_user):
    """Found live: a parent typed a nickname, not the registered display
    name - an exact-only match would have missed the other parent
    entirely, reproducing the original bug."""
    make_user(whatsapp_number="972500000001", display_name="יוסי")
    make_user(whatsapp_number="972500000002", display_name="רונית")
    ronit = _user("972500000002")

    with patch("src.webhook_handler.check_conflicts", return_value=[]), \
         patch("src.integrations.gmail.get_own_email_address", return_value="yossi@example.com"), \
         patch("src.webhook_handler.create_event") as mock_create:
        _handle_calendar(
            ronit, {"action": "create", "start": START, "end": END, "summary": "פגישה",
                     "attendee_names": ["יוסלה"]},
        )

    assert mock_create.call_args.kwargs["attendee_emails"] == ["yossi@example.com"]


def test_create_event_skips_a_name_that_is_not_a_registered_family_member(db_path, make_user):
    """An ordinary named person (not a bot user) - e.g. 'תמר' - has no
    Google account of their own for this bot to invite, so they're
    silently skipped, same as before this feature existed."""
    make_user(whatsapp_number="972500000001", display_name="יוסי")
    yossi = _user("972500000001")

    with patch("src.webhook_handler.check_conflicts", return_value=[]), \
         patch("src.webhook_handler.create_event") as mock_create:
        reply = _handle_calendar(
            yossi, {"action": "create", "start": START, "end": END, "summary": "פגישה",
                    "attendee_names": ["תמר"]},
        )

    assert mock_create.call_args.kwargs["attendee_emails"] == []
    assert "הזמנה" not in reply


def test_create_event_does_not_invite_the_requester_to_their_own_event(db_path, make_user):
    """If the requester's own name is (redundantly) in attendee_names, it
    must not try to invite them to their own event."""
    make_user(whatsapp_number="972500000001", display_name="יוסי")
    yossi = _user("972500000001")

    with patch("src.webhook_handler.check_conflicts", return_value=[]), \
         patch("src.webhook_handler.create_event") as mock_create:
        _handle_calendar(
            yossi, {"action": "create", "start": START, "end": END, "summary": "פגישה",
                    "attendee_names": ["יוסי"]},
        )

    assert mock_create.call_args.kwargs["attendee_emails"] == []


def test_create_event_falls_back_to_a_whatsapp_notice_when_the_attendee_is_not_connected(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי")
    make_user(whatsapp_number="972500000002", display_name="רונית")
    ronit = _user("972500000002")

    with patch("src.webhook_handler.check_conflicts", return_value=[]), \
         patch("src.integrations.gmail.get_own_email_address", side_effect=NotConnectedError("not connected")), \
         patch("src.webhook_handler.create_event") as mock_create, \
         patch("src.webhook_handler.send_text_message") as mock_send:
        reply = _handle_calendar(
            ronit, {"action": "create", "start": START, "end": END, "summary": "פגישה",
                     "attendee_names": ["יוסי"]},
        )

    assert mock_create.call_args.kwargs["attendee_emails"] == []  # no email - not invited
    mock_send.assert_called_once()
    assert mock_send.call_args.kwargs["to"] == "972500000001"
    assert "פגישה" in mock_send.call_args.kwargs["body"]
    assert "יוסי" in reply  # the requester still hears that something was attempted for him


def test_create_event_for_one_attendees_email_lookup_failing_does_not_break_the_primary_event(db_path, make_user):
    """Per-attendee error isolation (12.3) - a genuinely broken second
    account must never take down the requester's own event creation, or an
    exception escaping out of _handle_calendar."""
    make_user(whatsapp_number="972500000001", display_name="יוסי")
    make_user(whatsapp_number="972500000002", display_name="רונית")
    ronit = _user("972500000002")

    with patch("src.webhook_handler.check_conflicts", return_value=[]), \
         patch("src.integrations.gmail.get_own_email_address", side_effect=RuntimeError("boom")), \
         patch("src.webhook_handler.create_event") as mock_create, \
         patch("src.webhook_handler.send_text_message") as mock_send:
        reply = _handle_calendar(
            ronit, {"action": "create", "start": START, "end": END, "summary": "פגישה",
                     "attendee_names": ["יוסי"]},
        )

    assert "✅ נקבע" in reply  # the requester's own event still succeeded
    mock_create.assert_called_once()
    mock_send.assert_not_called()  # a genuine error, not a connection issue - no fallback notice


def test_create_event_with_no_attendee_names_behaves_exactly_as_before(db_path, make_user):
    make_user(whatsapp_number="972500000001", display_name="יוסי")
    yossi = _user("972500000001")

    with patch("src.webhook_handler.check_conflicts", return_value=[]), \
         patch("src.webhook_handler.create_event") as mock_create:
        reply = _handle_calendar(
            yossi, {"action": "create", "start": START, "end": END, "summary": "פגישה"},
        )

    mock_create.assert_called_once()
    assert mock_create.call_args.kwargs["attendee_emails"] == []
    assert "✅ נקבע" in reply

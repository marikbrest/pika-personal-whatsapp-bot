"""
Google Calendar API integration - reading and creating events.
Phase 2 - PRD section 6.

All functions require that the user has already connected Google
(src.integrations.google_oauth).
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.config import DEFAULT_TIMEZONE
from src.integrations.google_oauth import GoogleAuthExpiredError, NotConnectedError, get_credentials, is_genuine_auth_rejection


from src.i18n import t


def format_events_for_reply(events: list, timezone_name: str) -> str:
    """
    Formats a list of events (from list_events) as readable text (in the current locale) for
    WhatsApp. Never goes through Gemini - this is factual data, not a guess,
    so it is formatted directly in code.
    """
    if not events:
        return t("calendar.no_events")

    tz = ZoneInfo(timezone_name or DEFAULT_TIMEZONE)
    lines = []
    for event in events:
        start = event["start"]
        if start and "T" in start:
            dt = datetime.fromisoformat(start).astimezone(tz)
            time_str = dt.strftime("%H:%M")
        else:
            time_str = t("calendar.all_day")
        location = f" ({event['location']})" if event.get("location") else ""
        lines.append(f"🕐 {time_str} — {event['summary']}{location}")

    return "\n".join(lines)


# Per-process cache of the service object, to avoid rebuilding it (and opening
# a new transport/handshake) on every single calendar call. Key: user_id,
# value: (access_token, service) - rebuilt automatically if the token changed
# (i.e. after a refresh).
_service_cache: dict[int, tuple[str, object]] = {}


def _get_calendar_service(user_id: int):
    credentials = get_credentials(user_id)
    if credentials is None:
        raise NotConnectedError(f"user_id={user_id} has not connected Google yet")

    cached = _service_cache.get(user_id)
    if cached is not None and cached[0] == credentials.token:
        return cached[1]

    service = build("calendar", "v3", credentials=credentials)
    _service_cache[user_id] = (credentials.token, service)
    return service


def list_events(user_id: int, time_min: datetime, time_max: datetime, timezone_name: str):
    """
    Returns the events in the given time range (inclusive on both ends, per the
    Calendar API). time_min/time_max must be timezone-aware.
    Each event is returned as a dict: {id, summary, start, end, location}.
    id is the real Calendar event id - needed by find_event_by_match/
    update_event/check_conflicts to act on a specific event afterward.
    """
    try:
        service = _get_calendar_service(user_id)
        result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=time_min.isoformat(),
                timeMax=time_max.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                timeZone=timezone_name,
            )
            .execute()
        )
    except HttpError as e:
        if is_genuine_auth_rejection(e.resp.status, str(e)):
            raise GoogleAuthExpiredError(str(e)) from e
        raise

    events = []
    for item in result.get("items", []):
        start = item.get("start", {}).get("dateTime") or item.get("start", {}).get("date")
        end = item.get("end", {}).get("dateTime") or item.get("end", {}).get("date")
        events.append(
            {
                "id": item.get("id"),
                "summary": item.get("summary") or t("calendar.untitled"),
                "start": start,
                "end": end,
                "location": item.get("location"),
            }
        )
    return events


def create_event(
    user_id: int,
    summary: str,
    start: datetime,
    end: datetime,
    timezone_name: str,
    description: str | None = None,
    attendee_emails: list[str] | None = None,
) -> str:
    """
    Creates an event on the primary calendar. Returns the new event id.

    attendee_emails (2026-09-26): a REAL Google Calendar invite - each
    person gets it on their own calendar with accept/decline, and a later
    edit or cancellation by the organizer propagates to them automatically.
    Deliberately NOT the same as calling create_event a second time for
    each person - found live that an independently-created "duplicate"
    event can visibly conflict with a real invite the other person already
    has (e.g. one they sent manually), creating clutter that has to be
    manually cleaned up. sendUpdates="all" is what actually makes Google
    deliver the invite - omitting it silently adds the attendee to the
    event without notifying them at all.
    """
    try:
        service = _get_calendar_service(user_id)
        body = {
            "summary": summary,
            "start": {"dateTime": start.isoformat(), "timeZone": timezone_name},
            "end": {"dateTime": end.isoformat(), "timeZone": timezone_name},
        }
        if description:
            body["description"] = description
        if attendee_emails:
            body["attendees"] = [{"email": email} for email in attendee_emails]

        result = service.events().insert(
            calendarId="primary", body=body,
            sendUpdates="all" if attendee_emails else "none",
        ).execute()
        return result["id"]
    except HttpError as e:
        if is_genuine_auth_rejection(e.resp.status, str(e)):
            raise GoogleAuthExpiredError(str(e)) from e
        raise


def find_event_by_match(user_id: int, match: str, timezone_name: str, search_days: int = 21) -> dict | None:
    """
    Finds the soonest upcoming event whose summary matches `match`
    (case-insensitive substring) - same "identify by content, not position"
    principle as reminder_manage/task_manage, since event order shifts and
    there is no stable position to refer to. Searches from now to
    `search_days` ahead. Returns None when nothing matches, rather than
    guessing at the wrong event.
    """
    tz = ZoneInfo(timezone_name or DEFAULT_TIMEZONE)
    now = datetime.now(tz)
    time_max = now + timedelta(days=search_days)
    events = list_events(user_id, now, time_max, timezone_name)

    match_lower = match.lower()
    for event in events:
        if match_lower in (event["summary"] or "").lower():
            return event
    return None


def update_event(
    user_id: int,
    event_id: str,
    timezone_name: str,
    new_start: datetime | None = None,
    new_end: datetime | None = None,
    new_summary: str | None = None,
) -> None:
    """
    Moves and/or renames an existing event. A real PATCH - only the fields
    actually given are sent, so moving an event never wipes its location,
    description, or attendees the way a full replace would.
    """
    body: dict = {}
    if new_start is not None:
        body["start"] = {"dateTime": new_start.isoformat(), "timeZone": timezone_name}
    if new_end is not None:
        body["end"] = {"dateTime": new_end.isoformat(), "timeZone": timezone_name}
    if new_summary is not None:
        body["summary"] = new_summary

    try:
        service = _get_calendar_service(user_id)
        service.events().patch(calendarId="primary", eventId=event_id, body=body).execute()
    except HttpError as e:
        if is_genuine_auth_rejection(e.resp.status, str(e)):
            raise GoogleAuthExpiredError(str(e)) from e
        raise


def check_conflicts(
    user_id: int, start: datetime, end: datetime, timezone_name: str, exclude_event_id: str | None = None
) -> list[dict]:
    """
    Events already on the calendar that overlap [start, end) - the Calendar
    API's own timeMin/timeMax semantics already return anything overlapping
    the window, not just events fully contained in it, so this is really
    just list_events with one filter on top. exclude_event_id lets a
    reschedule check against everything EXCEPT the event being moved
    (otherwise an event always "conflicts" with its own current slot).
    """
    events = list_events(user_id, start, end, timezone_name)
    return [e for e in events if e.get("id") != exclude_event_id]

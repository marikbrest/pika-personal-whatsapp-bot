"""
The daily morning brief: calendar, weather, and email in a single message.

Design notes:
- Each section is fetched independently and failures are contained. A brief with
  two of three sections is far more useful than no brief at all, so a Google
  outage must not suppress the weather.
- Nothing here goes through Gemini. Every line is real data formatted in code,
  for the same reason as the calendar and weather handlers: a brief that
  hallucinates a meeting is worse than no brief.
"""
from src.config import DEFAULT_LOCATION, DEFAULT_TIMEZONE
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from src.i18n import t
from src.integrations.gmail import format_emails_for_reply, list_recent_emails
from src.integrations.google_calendar import format_events_for_reply, list_events
from src.integrations.google_oauth import GoogleAuthExpiredError, NotConnectedError
from src.integrations.weather import LocationNotFoundError, get_current_weather

DEFAULT_BRIEF_LOCATION = DEFAULT_LOCATION
MAX_BRIEF_EMAILS = 3


def _calendar_section(user_id: int, timezone_name: str) -> str | None:
    """
    Today's events. Raises NotConnectedError/GoogleAuthExpiredError (2026-09-16,
    no longer swallowed here) so a caller that needs to react differently to
    "not connected" can do so - specifically
    scheduler.check_and_send_daily_meetings_summaries, which sends the
    reconnect link right away instead of silently skipping the day's
    summary. build_morning_brief (the on-demand brief) still wants the old
    "just omit this section" behavior unchanged - see
    _calendar_section_or_none below, which it uses instead of calling this
    directly. Any OTHER exception is still caught and swallowed here
    (returns None, logged) - only Google's own "you need to reconnect"
    signal is worth surfacing distinctly to a caller.
    """
    tz = ZoneInfo(timezone_name or DEFAULT_TIMEZONE)
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1) - timedelta(seconds=1)

    try:
        events = list_events(user_id, start, end, timezone_name)
    except (NotConnectedError, GoogleAuthExpiredError):
        raise
    except Exception as e:
        print(f"[brief] calendar failed for user {user_id}: {e}")
        return None

    if not events:
        return t("brief.calendar_free")
    return t("brief.calendar_header") + format_events_for_reply(events, timezone_name)


def _calendar_section_or_none(user_id: int, timezone_name: str) -> str | None:
    """build_morning_brief's own wrapper around _calendar_section - restores
    the pre-2026-09-16 "not connected = just omit this section" behavior for
    the on-demand brief, which (unlike the proactive daily job) has no
    single clean place to put a reconnect link without breaking its
    "sections that fail are simply left out" design."""
    try:
        return _calendar_section(user_id, timezone_name)
    except (NotConnectedError, GoogleAuthExpiredError):
        return None


def _weather_section(location: str) -> str | None:
    try:
        weather = get_current_weather(location)
    except LocationNotFoundError:
        return None
    except Exception as e:
        print(f"[brief] weather failed: {e}")
        return None

    return t(
        "brief.weather", location=weather["location"], description=weather["description"],
        temperature=weather["temperature"], feels_like=weather["feels_like"],
    )


def _email_section(user_id: int) -> str | None:
    """Unread email only - the brief should surface what needs attention, not everything."""
    try:
        emails = list_recent_emails(user_id, query="is:unread", max_results=MAX_BRIEF_EMAILS)
    except (NotConnectedError, GoogleAuthExpiredError):
        return None
    except Exception as e:
        print(f"[brief] email failed for user {user_id}: {e}")
        return None

    if not emails:
        return t("brief.no_email")
    return t("brief.email_header", count=len(emails)) + format_emails_for_reply(emails)


def build_morning_brief(user_id: int, timezone_name: str, display_name: str | None = None,
                        location: str = DEFAULT_BRIEF_LOCATION) -> str:
    """
    Assembles the full brief. Sections that fail or are unavailable are simply
    left out rather than replaced with error text - the brief should read
    cleanly, and the underlying failure is already in the log.
    """
    greeting = t("greeting.morning", name=t("greeting.name_suffix", name=display_name) if display_name else "")

    sections = [
        _weather_section(location),
        _calendar_section_or_none(user_id, timezone_name),
        _email_section(user_id),
    ]
    present = [s for s in sections if s]

    if not present:
        # Everything failed or nothing is connected - say so plainly instead of
        # sending a greeting with no content.
        return f"{greeting}\n\n" + t("brief.nothing_available")

    return greeting + "\n\n" + "\n\n".join(present)

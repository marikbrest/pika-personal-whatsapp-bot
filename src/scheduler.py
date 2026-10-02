"""
Reminders: computing next_trigger_at (12.2) plus the actual delivery loop.

Weekdays are represented as three lowercase English letters: mon, tue, wed,
thu, fri, sat, sun (so schedule_days in the reminders table looks like
"mon,wed,fri").
"""
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from src.config import DEFAULT_TIMEZONE

_WEEKDAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_HEBREW_DAY_NAMES = {
    "mon": "שני", "tue": "שלישי", "wed": "רביעי", "thu": "חמישי",
    "fri": "שישי", "sat": "שבת", "sun": "ראשון",
}

# WhatsApp Cloud API language code for the Hebrew templates submitted in
# WhatsApp Manager (reminder_notification, package_status_update,
# google_reconnect_needed) - must match what each template was approved
# under, not just "Hebrew" as shown in the UI.
_TEMPLATE_LANGUAGE_CODE = "he"

# Default per-user daily-meetings-summary send time (a user can set their
# own via manage_daily_meetings_summary - see src/tools/batch13.py; this is
# only what a brand-new opt-in defaults to). Imported into
# webhook_handler._handle_daily_meetings_summary too, so both stay in sync.
DEFAULT_DAILY_MEETINGS_SUMMARY_TIME = "07:00"
# How often check_and_send_daily_meetings_summaries polls for due users -
# see that function's own docstring for why this moved from a single fixed
# cron slot to a frequent interval check.
DAILY_MEETINGS_SUMMARY_CHECK_INTERVAL_MINUTES = 5


def format_schedule_description(
    schedule_type: str, schedule_time: str, schedule_days: str | None, timezone_name: str
) -> str:
    """
    Describes a reminder in readable Hebrew, for the "what do I have scheduled"
    listing. Unrelated to computing next_trigger_at itself - this is purely a
    human-readable description.
    """
    if schedule_type == "once":
        local_dt = datetime.fromisoformat(schedule_time).replace(tzinfo=ZoneInfo(timezone_name or DEFAULT_TIMEZONE))
        return f"חד-פעמי, {local_dt.strftime('%d/%m ב-%H:%M')}"

    if schedule_type == "daily":
        return f"כל יום ב-{schedule_time}"

    if schedule_type == "weekly":
        days = [d.strip().lower() for d in (schedule_days or "").split(",") if d.strip()]
        days_hebrew = ", ".join(_HEBREW_DAY_NAMES.get(d, d) for d in days)
        return f"כל {days_hebrew} ב-{schedule_time}"

    return f"{schedule_type} {schedule_time}"


def compute_next_trigger(
    schedule_type: str,
    schedule_time: str,
    schedule_days: str | None,
    timezone_name: str,
    now: datetime | None = None,
) -> datetime:
    """
    Computes the next next_trigger_at, in UTC (for consistent storage in the DB).

    schedule_type:
      - "once":   schedule_time is a full ISO datetime, e.g. "2026-08-05T09:00:00"
                  (in the user's local time, without a timezone)
      - "daily":  schedule_time is "HH:MM"
      - "weekly": schedule_time is "HH:MM" and schedule_days is a comma-separated day list

    Important: this always computes relative to `now` (not to the previous
    next_trigger_at). That is exactly what gives catch-up for free (PRD 12.2):
    even if a daily reminder was missed for three days because the machine was
    off, the next call returns the nearest future trigger rather than replaying
    every missed day.

    Returns a datetime with tzinfo=UTC.
    """
    tz = ZoneInfo(timezone_name or DEFAULT_TIMEZONE)
    now_local = (now or datetime.now(ZoneInfo("UTC"))).astimezone(tz)

    if schedule_type == "once":
        naive = datetime.fromisoformat(schedule_time)
        local_dt = naive.replace(tzinfo=tz)
        return local_dt.astimezone(ZoneInfo("UTC"))

    if schedule_type == "daily":
        hour, minute = map(int, schedule_time.split(":"))
        candidate = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now_local:
            candidate += timedelta(days=1)
        return candidate.astimezone(ZoneInfo("UTC"))

    if schedule_type == "weekly":
        hour, minute = map(int, schedule_time.split(":"))
        days = [d.strip().lower() for d in (schedule_days or "").split(",") if d.strip()]
        if not days:
            raise ValueError("a weekly reminder requires schedule_days")
        target_weekdays = sorted(_WEEKDAY_NAMES.index(d) for d in days)

        for offset in range(8):  # at most one week ahead
            candidate_date = now_local + timedelta(days=offset)
            if candidate_date.weekday() in target_weekdays:
                candidate = candidate_date.replace(
                    hour=hour, minute=minute, second=0, microsecond=0
                )
                if candidate > now_local:
                    return candidate.astimezone(ZoneInfo("UTC"))
        raise ValueError("no matching day found in the coming week - check schedule_days")

    raise ValueError(f"unsupported schedule_type: {schedule_type}")


def notify_reminder_delivery_failure(recipient_name: str, content: str, owner_number: str) -> None:
    """
    2026-09-19: sends the "couldn't deliver this reminder" notice to the
    reminder's own owner AND to every user opted into
    notify_on_reminder_delivery_failure (both parents, per the admin's own
    request the same day the async 24h-window fallback fix shipped - a
    reminder to a kid can now fail for good only after that retry has
    ALSO failed, and both parents want to hear about it regardless of
    which of them created that specific reminder). Deduplicated: if the
    owner is already in that notify list (the common case), they get
    exactly one copy, not two.

    Shared by both places a reminder to a contact can finally, genuinely
    fail: check_and_send_reminders's own synchronous failure branch, and
    whatsapp.handle_delivery_status's async on_permanent_failure callback
    (the 24h-window template retry itself failing).
    """
    from src.db.models import list_reminder_delivery_failure_notification_numbers
    from src.integrations.whatsapp import send_text_message

    notice = (
        f"⚠️ לא הצלחתי למסור את התזכורת ל-{recipient_name}: \"{content}\". "
        f"כנראה כי הוא/היא לא שלחו הודעה לבוט ב-24 השעות האחרונות "
        f"(מגבלה של WhatsApp). כדאי לבקש מהם לשלוח לבוט הודעה כלשהי."
    )
    recipients = {owner_number, *list_reminder_delivery_failure_notification_numbers()}
    for number in recipients:
        send_text_message(to=number, body=notice)


def check_and_send_reminders() -> None:
    """
    Called every 60 seconds by the BackgroundScheduler.
    Finds due reminders, sends the message, and updates or deactivates each one.

    PRD 12.2 - if a user has several reminders due in the same check (for
    example after the machine was off), they are grouped into a single
    consolidated message instead of flooding the chat.
    """
    # Late import to avoid a circular import (models only imports from config,
    # but whatsapp/scheduler may be loaded before the app is ready)
    from src.db.models import (
        deactivate_reminder,
        get_due_reminders,
        save_outgoing_message,
        update_reminder_next_trigger,
    )
    from src.integrations.whatsapp import send_text_or_template

    now_utc = datetime.now(ZoneInfo("UTC"))
    due = get_due_reminders(now_utc.isoformat())
    if not due:
        return

    by_destination: dict[str, list] = defaultdict(list)
    for row in due:
        by_destination[row["destination_number"]].append(row)

    for destination_number, reminders in by_destination.items():
        # Per-recipient error isolation (12.3): a failure for one recipient must not block the rest
        try:
            # kid_facing_role ("אבא"/"אימא"), not display_name - this
            # prefix only ever appears in a message TO a kid (is_for_contact
            # below), never one addressed to the parent themselves. Falls
            # back to display_name if unset (every user without a
            # kid_facing_role of their own).
            owner_name = reminders[0]["owner_kid_facing_role"] or reminders[0]["owner_display_name"]
            is_for_contact = reminders[0]["recipient_name"] is not None
            prefix = f" מ-{owner_name}" if (is_for_contact and owner_name) else ""

            if len(reminders) == 1:
                body = f"🔔 תזכורת{prefix}: {reminders[0]['content']}"
                template_content = f"{reminders[0]['content']}{prefix}"
            else:
                lines = "\n".join(f"- {r['content']}" for r in reminders)
                body = f"🔔 יש לך{prefix} {len(reminders)} תזכורות:\n{lines}"
                joined = "; ".join(r["content"] for r in reminders)
                template_content = f"{len(reminders)} תזכורות{prefix}: {joined}"

            # reminder_notification is a pre-approved Utility template (see
            # WHATSAPP_ACCESS_TOKEN / WhatsApp Manager) - it lets a reminder
            # actually reach a recipient who hasn't messaged the bot in the
            # last 24h, which free text alone cannot (WhatsApp's own policy).
            # Before Meta approves the template this behaves exactly like the
            # old send_text_message call always did (the template send also
            # fails, same net result); once approved, delivery starts working
            # with no further code change.
            owner_number = reminders[0]["owner_number"]
            recipient = reminders[0]["recipient_name"]

            # 2026-09-19: if send_text_or_template's own 24h-window fallback
            # retry only fails later, asynchronously (see its docstring),
            # this is what finally notifies both parents - the synchronous
            # `if not sent` branch right below can't see that failure at
            # all, since by then it hasn't happened yet.
            #
            # 2026-09-25: the three values are bound as DEFAULT ARGUMENTS,
            # not captured by closure. This lambda is invoked long after
            # this loop has finished (from whatever later webhook request
            # carries the failed delivery status), and recipient/
            # template_content/owner_number are all rebound on every
            # iteration - a plain closure would report whichever kid
            # happened to be last in the loop, for every failure.
            on_permanent_failure = (
                (lambda r=recipient, c=template_content, o=owner_number: notify_reminder_delivery_failure(r, c, o))
                if is_for_contact else None
            )

            sent = send_text_or_template(
                to=destination_number,
                body=body,
                template_name="reminder_notification",
                language_code=_TEMPLATE_LANGUAGE_CODE,
                body_params=[template_content],
                on_permanent_failure=on_permanent_failure,
            )
            if not sent:
                if is_for_contact:
                    # Delivery failure to a contact is most likely permanent
                    # (WhatsApp's 24-hour window policy: the recipient has not
                    # messaged the bot recently, and the template fallback
                    # above already tried and also failed - e.g. not yet
                    # approved). Retrying every 60 seconds forever would only
                    # spam the logs and delay others. Instead: notify the
                    # owner (and co-parent) and advance/deactivate the reminder.
                    notify_reminder_delivery_failure(recipient, template_content, owner_number)
                    save_outgoing_message(reminders[0]["user_id"], f"⚠️ לא הצלחתי למסור את התזכורת ל-{recipient}.")
                    for r in reminders:
                        if r["schedule_type"] == "once":
                            deactivate_reminder(r["id"])
                        else:
                            next_trigger = compute_next_trigger(
                                schedule_type=r["schedule_type"],
                                schedule_time=r["schedule_time"],
                                schedule_days=r["schedule_days"],
                                timezone_name=r["timezone"],
                                now=now_utc,
                            )
                            update_reminder_next_trigger(r["id"], next_trigger)
                    continue

                # Failure delivering to the user themselves is probably transient (network) - retry next cycle
                print(f"[scheduler] send failed for {destination_number} - will retry next cycle")
                continue

            # Record in the owner's history (not the recipient's - they are not
            # necessarily a registered user) so the bot "knows" it sent a
            # reminder if someone replies to it
            save_outgoing_message(reminders[0]["user_id"], body)

            for r in reminders:
                if r["schedule_type"] == "once":
                    deactivate_reminder(r["id"])
                else:
                    next_trigger = compute_next_trigger(
                        schedule_type=r["schedule_type"],
                        schedule_time=r["schedule_time"],
                        schedule_days=r["schedule_days"],
                        timezone_name=r["timezone"],
                        now=now_utc,
                    )
                    update_reminder_next_trigger(r["id"], next_trigger)
        except Exception as e:
            print(f"[scheduler] unexpected error for {destination_number}: {e}")


_ABANDONED_PACKAGE_STATUS = "לא נמצא"
_TERMINAL_PACKAGE_STATUSES = {"delivered", "exception", _ABANDONED_PACKAGE_STATUS}
_PACKAGE_CHECK_INTERVAL = timedelta(hours=24)

# Ship24 reports no milestone at all for a tracking number no carrier has
# registered yet (a typo, or a number issued before the parcel was handed over).
# That is not a terminal status, so without this rule such a package would be
# re-checked once a day forever - ~30 of the 100 free monthly calls burned on a
# number that will never resolve. A real parcel normally registers within a few
# days of the shipping email, so we keep trying for a week and then stop.
_UNKNOWN_PACKAGE_STATUS = "לא ידוע"
_UNKNOWN_GIVE_UP_AFTER = timedelta(days=7)


def _has_never_registered(pkg, now_utc) -> bool:
    """
    True when a package has sat at the unknown status since it was first added
    and that was longer than _UNKNOWN_GIVE_UP_AFTER ago - i.e. no carrier ever
    picked it up and no further check is worth the quota.

    Fails open (returns False, keep checking) on an unparseable created_at:
    silently dropping a real package is worse than spending one API call.
    """
    if pkg["last_status"] != _UNKNOWN_PACKAGE_STATUS:
        return False
    raw = pkg["created_at"]
    if not raw:
        return False
    try:
        created = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        print(f"[scheduler] unparseable created_at on package {pkg['id']}: {raw!r}")
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=ZoneInfo("UTC"))  # sqlite CURRENT_TIMESTAMP is UTC
    return (now_utc - created) > _UNKNOWN_GIVE_UP_AFTER


def check_and_notify_package_changes() -> None:
    """
    Called every hour by the BackgroundScheduler. Checks each tracked
    package's live Ship24 status at most once every 24h - staggered per
    package from whenever it was first found/last checked (get_due_for_check's
    cutoff, not a fixed daily clock time) - and sends a WhatsApp notification
    only when the status actually changed since the last check, never on
    every check, so a package sitting in the same state doesn't generate
    daily noise.

    Also stops checking a package that has sat at the unknown status since it
    was added more than a week ago (see _has_never_registered) - it was never
    picked up by any carrier and never will be.

    Stops checking a package once it reaches a terminal status (delivered/
    exception): Ship24's free tier is 100 calls/month shared with on-demand
    "where's my package" queries (see webhook_handler._handle_package_status),
    so an already-delivered package must not keep consuming quota forever.
    """
    from src.db.models import get_packages_due_for_check, update_package_status
    from src.integrations.shipping import ShippingNotConfiguredError, get_tracking_status
    from src.integrations.whatsapp import send_text_message, send_text_or_template

    now_utc = datetime.now(ZoneInfo("UTC"))
    cutoff = (now_utc - _PACKAGE_CHECK_INTERVAL).isoformat()

    for pkg in get_packages_due_for_check(cutoff):
        if pkg["last_status"] in _TERMINAL_PACKAGE_STATUSES:
            continue
        if _has_never_registered(pkg, now_utc):
            # Skipped before spending an API call. Recording the abandoned status
            # (rather than just `continue`-ing) makes this terminal, so the user
            # is told exactly once instead of every hour, and the row is never
            # polled again.
            # No approved template covers this specific message, so it stays
            # send_text_message-only - it will simply not be delivered if the
            # recipient is outside the 24h window, same as before.
            label = pkg["description"] or pkg["tracking_number"]
            update_package_status(pkg["id"], _ABANDONED_PACKAGE_STATUS)
            send_text_message(
                to=pkg["whatsapp_number"],
                body=(
                    f"📦 הפסקתי לעקוב אחרי {label}\n"
                    f"אף חברת שילוח לא רשמה את מספר המעקב הזה במשך שבוע, "
                    f"אז כנראה שהוא שגוי או שהחבילה עוד לא נמסרה לשילוח. "
                    f"אם תשלח לי אותו שוב אתחיל לעקוב מחדש."
                ),
            )
            continue
        try:
            result = get_tracking_status(pkg["tracking_number"])
        except ShippingNotConfiguredError:
            return  # not configured at all right now - nothing to do for anyone
        except Exception as e:
            # Per-package error isolation (12.3 principle) - one bad tracking
            # number or a transient Ship24 error must not block everyone else's checks
            print(f"[scheduler] package status check failed for package {pkg['id']}: {e}")
            continue

        old_status = pkg["last_status"]
        new_status = result["status_milestone"] or _UNKNOWN_PACKAGE_STATUS
        update_package_status(pkg["id"], new_status)

        if old_status is not None and new_status != old_status:
            label = pkg["description"] or pkg["tracking_number"]
            body = f"📦 עדכון משלוח: {label}\nהסטטוס השתנה מ-{old_status} ל-{new_status}"
            # package_status_update is a pre-approved Utility template -
            # falls back to it automatically when the recipient is outside
            # the 24h window; see check_and_send_reminders for the same
            # pattern and why it's safe before/after Meta approval.
            send_text_or_template(
                to=pkg["whatsapp_number"],
                body=body,
                template_name="package_status_update",
                language_code=_TEMPLATE_LANGUAGE_CODE,
                body_params=[label, old_status, new_status],
            )


# Notified-at most once a day per user even if the job somehow runs more often
# (e.g. a restart storm). In-memory only: losing this on restart just means one
# extra message, which is the harmless direction to fail in.
_token_alert_sent_on: dict[int, str] = {}


def check_google_token_health() -> None:
    """
    Runs once a day. Forces a refresh-token exchange for every user who has
    connected Google and WhatsApps them a reconnect link if it has died.

    Why this exists: on 2026-09-04 the stored refresh_token was revoked on
    Google's side and the first anyone knew of it was calendar and mail silently
    failing for a day. Google's own errors only surface when a user happens to
    ask for something; this turns a silent breakage into a message that arrives
    before the feature is needed.

    force_refresh=True matters here - without it a cached, still-valid access
    token would make a dead refresh_token look perfectly healthy.
    """
    from src.db.models import admin_list_users
    from src.integrations.google_oauth import (
        GoogleAuthExpiredError,
        build_auth_url,
        get_credentials,
    )
    from src.integrations.whatsapp import send_text_or_template

    today = datetime.now(ZoneInfo("UTC")).date().isoformat()

    for user in admin_list_users():
        if not user["is_active"]:
            continue
        try:
            credentials = get_credentials(user["id"], force_refresh=True)
            if credentials is None:
                continue  # never connected Google - nothing to check
            _token_alert_sent_on.pop(user["id"], None)  # healthy again, re-arm
        except GoogleAuthExpiredError as e:
            # 2026-09-18: same reasoning as the daily-summary alert path -
            # log the real error text so a future occurrence is diagnosable
            # from what actually triggered the classification, not guesswork.
            print(f"[scheduler] token health check: Google auth expired for user {user['id']}: {e}")
            if _token_alert_sent_on.get(user["id"]) == today:
                continue
            _token_alert_sent_on[user["id"]] = today
            try:
                auth_url = build_auth_url(user["id"])
                body = (
                    "⚠️ החיבור לחשבון Google שלך פג ויש להתחבר מחדש.\n"
                    "עד אז היומן והמייל לא יעבדו. קישור לחיבור מחדש:\n"
                    f"{auth_url}"
                )
                # google_reconnect_needed is a pre-approved Utility template -
                # same fallback pattern as check_and_send_reminders. This alert
                # exists specifically because the user may not have messaged
                # the bot recently (that's often *why* the token expired
                # unnoticed), so the 24h window is a real risk here, not a
                # theoretical one.
                send_text_or_template(
                    to=user["whatsapp_number"],
                    body=body,
                    template_name="google_reconnect_needed",
                    language_code=_TEMPLATE_LANGUAGE_CODE,
                    body_params=[auth_url],
                )
                print(f"[scheduler] google token expired for user {user['id']} - reconnect link sent")
            except Exception as e:
                print(f"[scheduler] could not notify user {user['id']} about expired token: {e}")
        except Exception as e:
            # Per-user isolation: a transient network error for one user must not
            # stop the others being checked, and must not look like an expiry.
            print(f"[scheduler] google token check failed for user {user['id']}: {e}")


_WATCH_CHECK_INTERVAL = timedelta(hours=1)


def check_watches() -> None:
    """
    Called hourly by the BackgroundScheduler. The generic form of
    check_and_notify_package_changes: for every active watch due a check
    (staggered from whenever it was created/last checked, same technique as
    packages), runs its type's checker (src.integrations.watchers.CHECKERS),
    and notifies the owner only when the state actually changed since the
    last check - never on every check, and never on the very first check
    after creation (that just establishes the baseline, see add_watch).

    Per-watch error isolation (12.3 principle, same as packages): one bad
    URL or a transient Gmail error must not block everyone else's checks.
    """
    from src.db.models import get_watches_due_for_check, update_watch_state
    from src.integrations.watchers import CHECKERS, NOTIFY_MESSAGES
    from src.integrations.whatsapp import send_text_message

    now_utc = datetime.now(ZoneInfo("UTC"))
    cutoff = (now_utc - _WATCH_CHECK_INTERVAL).isoformat()

    for watch in get_watches_due_for_check(cutoff):
        checker = CHECKERS.get(watch["watch_type"])
        if checker is None:
            continue  # unknown watch_type (e.g. rolled back code after adding one) - skip, don't crash

        try:
            new_state = checker(watch["user_id"], watch["target"])
        except Exception as e:
            print(f"[scheduler] watch check failed for watch {watch['id']}: {e}")
            continue

        if new_state is None:
            continue  # transient failure - try again next cycle, state untouched

        old_state = watch["last_state"]
        update_watch_state(watch["id"], new_state)

        if old_state is not None and new_state != old_state:
            label = watch["label"] or watch["target"]
            body = NOTIFY_MESSAGES[watch["watch_type"]](label)
            send_text_message(to=watch["whatsapp_number"], body=body)


def check_and_send_kids_schedule_reminders() -> None:
    """
    Called once a day, evening local time (see start_scheduler_loop) - for
    every user who has saved at least one kids_schedule entry, sends that
    evening's "tomorrow's timetable" message covering however many kids
    they've saved.

    "Tomorrow" is computed in the user's OWN timezone (users.timezone), not
    the job's own Asia/Jerusalem trigger time - same principle
    check_and_send_reminders already applies per-reminder via
    compute_next_trigger(timezone_name=r["timezone"]). In practice this bot's
    users are all Israel-based, but there's no reason to hardcode that here
    when the column already exists.

    Per-user error isolation (12.3 principle, same as reminders/packages/
    watches): one user's bad timezone string or a transient send failure
    must not block the rest.

    No approved WhatsApp template exists yet for this (same position as
    watch notifications) - plain text; a template submission, not a code
    change, if this ever needs to reliably reach someone outside the 24h
    window.
    """
    from src.db.models import get_kids_schedule_for_day, list_users_with_kids_schedule
    from src.integrations.whatsapp import send_text_message

    for user in list_users_with_kids_schedule():
        try:
            tz = ZoneInfo(user["timezone"] or DEFAULT_TIMEZONE)
            tomorrow = datetime.now(tz) + timedelta(days=1)
            day_of_week = _WEEKDAY_NAMES[tomorrow.weekday()]

            rows = get_kids_schedule_for_day(user["user_id"], day_of_week)
            if not rows:
                continue

            blocks = [f"*{r['kid_name']}:*\n{r['content']}" for r in rows]
            body = f"🎒 מערכת ליום {_HEBREW_DAY_NAMES[day_of_week]} (מחר):\n\n" + "\n\n".join(blocks)
            send_text_message(to=user["whatsapp_number"], body=body)
        except Exception as e:
            print(f"[scheduler] kids schedule reminder failed for user {user['user_id']}: {e}")


def check_and_send_daily_meetings_summaries() -> None:
    """
    Called every DAILY_MEETINGS_SUMMARY_CHECK_INTERVAL_MINUTES minutes (see
    start_scheduler_loop) - for every user who opted in
    (users.daily_meetings_summary_enabled), checks whether "now" in their
    OWN timezone has reached their OWN configured send time
    (daily_meetings_summary_time, 'HH:MM', default '07:00' - see
    src/tools/batch13.py for how a user changes it) and today's send hasn't
    already gone out, and if so sends today's calendar summary.

    2026-09-17: the send time became per-user configurable (was a single
    fixed 07:00 for everyone) - the admin asked for this after the other
    parent wanted 9:00 and the bot could only offer a flat 7:00. A single
    daily cron slot
    can't serve N different per-user times, so this switched from one
    fixed-hour cron trigger to a frequent interval check, with
    daily_meetings_summary_last_sent_date (the user's own local date) as an
    explicit "already handled today" marker - without it, this would
    re-send (or re-alert) on every single poll once the target time has
    passed for the day, not just once.

    Reuses morning_brief._calendar_section rather than reimplementing the
    fetch/format - same real-data-only, no-Gemini formatting the on-demand
    morning brief already uses.

    2026-09-16: unlike the on-demand brief (which just omits a failed
    section), a "calendar sync failed" situation here gets an explicit
    message WITH the reconnect link right away, rather than the user only
    finding out incidentally via the next day's 06:00 UTC
    check_google_token_health run. Only NotConnectedError/
    GoogleAuthExpiredError get this treatment - _calendar_section still
    swallows any OTHER, non-auth failure internally and returns None for
    it; that case is deliberately NOT marked as "sent today", so it keeps
    retrying at the next poll rather than waiting until tomorrow (a
    transient failure should recover within minutes, not a full day).

    2026-09-17: retries the fetch ONCE before concluding it's a genuine auth
    failure - found live that a single transient hiccup talking to Google
    (get_credentials(force_refresh=True) doing a real network round-trip to
    Google's token endpoint, which can fail transiently like any network
    call) was enough to trigger the "you need to reconnect" message even
    though the connection was completely healthy moments later (confirmed
    by manually re-running the exact same fetch right after a real alert
    fired - it succeeded cleanly). A single flaky attempt telling the user
    their Google connection is dead is worse than a few extra seconds of
    delay, so now only a failure that repeats on retry gets reported (and,
    once reported, IS marked as sent-today - alerting every few minutes for
    a genuinely dead connection would be its own kind of spam).

    Per-user error isolation (12.3 principle, same as every other proactive
    job here).
    """
    from src.db.models import list_users_with_daily_meetings_summary_enabled, mark_daily_meetings_summary_sent
    from src.integrations.google_oauth import GoogleAuthExpiredError, NotConnectedError, build_auth_url
    from src.integrations.whatsapp import send_text_message
    from src.morning_brief import _calendar_section

    for user in list_users_with_daily_meetings_summary_enabled():
        try:
            tz = ZoneInfo(user["timezone"] or DEFAULT_TIMEZONE)
            now = datetime.now(tz)
            today_str = now.date().isoformat()

            if user["daily_meetings_summary_last_sent_date"] == today_str:
                continue  # already sent (or already alerted) today

            target_time = user["daily_meetings_summary_time"] or "07:00"
            hour, minute = (int(p) for p in target_time.split(":"))
            target_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if now < target_dt:
                continue  # not due yet today

            try:
                try:
                    calendar_text = _calendar_section(user["user_id"], user["timezone"] or DEFAULT_TIMEZONE)
                except (NotConnectedError, GoogleAuthExpiredError):
                    time.sleep(3)
                    calendar_text = _calendar_section(user["user_id"], user["timezone"] or DEFAULT_TIMEZONE)
            except (NotConnectedError, GoogleAuthExpiredError) as e:
                # 2026-09-18: log the real underlying error text before
                # alerting the user - the classification in get_credentials
                # is itself text-based (invalid_grant/invalid_token/etc. vs
                # everything else), so if this alert ever fires for what
                # turns out to have been a transient condition, this is the
                # only way to tell which specific error text triggered it,
                # rather than guessing after the connection has already
                # recovered on its own (as it did the very first time this
                # was investigated, on 2026-09-17/18).
                print(f"[scheduler] daily meetings summary: Google auth failed twice for user {user['user_id']} ({type(e).__name__}): {e}")
                auth_url = build_auth_url(user["user_id"])
                body = (
                    "⚠️ סנכרון יומן גוגל נכשל - לא הצלחתי לשלוח לך את סיכום הפגישות היום כי "
                    f"החיבור שלך לגוגל לא פעיל.\nאפשר להתחבר מחדש כאן: {auth_url}"
                )
                send_text_message(to=user["whatsapp_number"], body=body)
                mark_daily_meetings_summary_sent(user["user_id"], today_str)
                continue

            if calendar_text is None:
                continue  # some other, non-auth failure - retry at the next poll, not marked as sent

            greeting = f"☀️ בוקר טוב{', ' + user['display_name'] if user['display_name'] else ''}!"
            send_text_message(to=user["whatsapp_number"], body=f"{greeting}\n\n{calendar_text}")
            mark_daily_meetings_summary_sent(user["user_id"], today_str)
        except Exception as e:
            print(f"[scheduler] daily meetings summary failed for user {user['user_id']}: {e}")


def _next_persistent_reminder_occurrence(row) -> datetime:
    """
    Shared by check_and_send_persistent_reminders (the escalation path) and
    webhook_handler._check_task_confirmation (the confirmation path) -
    both need to compute a RECURRING persistent reminder's next occurrence
    the exact same way, so a daily/weekly reminder resets consistently
    regardless of which of the two paths resolved this cycle. Reuses
    compute_next_trigger directly - same schedule_type/schedule_time/
    schedule_days convention as ordinary reminders - in the OWNER's own
    timezone (not the recipient's), since the owner is the one who set the
    schedule up ("every evening at 8" means 8pm where the parent lives).

    row must have schedule_type/schedule_time/schedule_days/owner_timezone
    - both get_due_persistent_reminders and
    find_pending_persistent_reminder_for_number select these.
    """
    return compute_next_trigger(
        schedule_type=row["schedule_type"],
        schedule_time=row["schedule_time"],
        schedule_days=row["schedule_days"],
        timezone_name=row["owner_timezone"] or DEFAULT_TIMEZONE,
    )


def _generate_creative_reminder_text(content: str, recipient_name: str, owner_name: str) -> str:
    """
    The admin and the kids asked for persistent-reminder nags to
    sound like an actual (lightly cheeky, emoji-heavy) parent nagging -
    "נו, מה קורה עם הסדר" - instead of the exact same templated line every
    single attempt. One lightweight Gemini call per nag (same shape as
    _pick_reaction_emoji/webhook_handler._check_task_confirmation)
    generates a fresh line each time; the actual confirmation instruction
    is appended separately, fixed and reliable, never left to the model to
    remember to include. Falls back to the original plain templated text
    on ANY Gemini failure - a nag that arrives on time with boring
    phrasing is far better than a nag that doesn't arrive because a
    creative-writing call failed.
    """
    from src.integrations.gemini import call_gemini_json

    prompt = (
        "אתה כותב הודעת תזכורת קצרה (משפט אחד, לכל היותר שניים) בעברית מהורה לילד/ה, "
        "מזכיר/ה לו/ה לעשות משהו שכבר ביקשו ממנו/ה ועדיין לא בוצע. "
        "הטון: הורה קליל, קצת מצחיק/עוקצני (\"נו, מה קורה עם...\", \"עדיין מחכה ל...\"), "
        "עם אימוג'ים, קצת \"בוטה\"/ישיר אבל בשום אופן לא פוגעני או מעליב. "
        "תהיה/י יצירתי/ת - נסח/י אחרת בכל פעם, לא ניסוח שגרתי קבוע.\n"
        f'המשימה שהתבקש/ה לעשות: "{content}"\n'
        f"שם הילד/ה: {recipient_name}\n"
        f"שם ההורה ששלח את התזכורת: {owner_name}\n"
        'החזר אך ורק JSON בפורמט: {"message": "..."}'
    )
    try:
        result = call_gemini_json(prompt)
        message = (result or {}).get("message")
        if message:
            return f"{message}\n\nכשעשית, תגיד לי \"עשיתי\" 👍"
    except Exception as e:
        print(f"[scheduler] creative reminder text generation failed (non-fatal): {e}")

    return f"🔔 תזכורת מ-{owner_name}: {content}\n\nכשעשית, תגיד לי \"עשיתי\" 👍"


def check_and_send_persistent_reminders() -> None:
    """
    Called every 60 seconds by the BackgroundScheduler (see
    start_scheduler_loop) - for every persistent_reminders row that's due
    (next_trigger_at <= now):
      - if it hasn't reached max_attempts yet, sends the nag to the
        recipient and reschedules itself retry_interval_minutes later
        (advance_persistent_reminder), so the SAME row keeps firing until
        either the recipient confirms (see
        webhook_handler._check_task_confirmation, which marks it 'done' and
        removes it from future due-checks entirely) or attempts_sent
        reaches max_attempts;
      - once max_attempts is reached with still no confirmation, sends the
        escalation to the OWNER instead (the parent who created it). For a
        'once' reminder that's terminal (mark_persistent_reminder_escalated)
        - the nagging stops there, on the assumption the parent will follow
        up in person rather than the bot nagging forever. For a recurring
        (daily/weekly) reminder (2026-09-17), the row resets itself for its
        NEXT occurrence instead (reschedule_persistent_reminder_for_next_
        occurrence) - one unconfirmed day/week does not kill tomorrow's
        cycle, it just tells the parent about today's.

    New feature (2026-09-17): the admin asked for reminders to the kids that
    "won't get missed" and "won't stop until they confirm they did it" -
    this is the proactive delivery half; src/tools/batch14.py is the
    chat-driven creation/list/cancel side. Same-day follow-up: recurring
    schedules ("כל יום בערב" / "כל יום א,ב,ג") - see
    _next_persistent_reminder_occurrence, shared with
    webhook_handler._check_task_confirmation so both the escalation path
    here and the confirmation path there compute a recurring reminder's
    next occurrence the exact same way.

    Per-user (per-row) error isolation (12.3 principle, same as every other
    proactive job here).
    """
    from src.db.models import (
        advance_persistent_reminder,
        get_due_persistent_reminders,
        mark_persistent_reminder_escalated,
        reschedule_persistent_reminder_for_next_occurrence,
    )
    from src.integrations.whatsapp import send_text_message, send_text_or_template

    now_utc = datetime.now(ZoneInfo("UTC"))
    for row in get_due_persistent_reminders(now_utc.isoformat()):
        try:
            if row["attempts_sent"] >= row["max_attempts"]:
                body = (
                    f"⚠️ {row['recipient_name']} עדיין לא אישר/ה ביצוע של: \"{row['content']}\" "
                    f"אחרי {row['max_attempts']} תזכורות. כדאי לבדוק בעצמך."
                )
                send_text_message(to=row["owner_whatsapp_number"], body=body)
                if row["schedule_type"] == "once":
                    mark_persistent_reminder_escalated(row["id"])
                else:
                    next_occurrence = _next_persistent_reminder_occurrence(row)
                    reschedule_persistent_reminder_for_next_occurrence(row["id"], next_occurrence)
                continue

            # kid_facing_role ("אבא"/"אימא"), same reasoning as
            # check_and_send_reminders' own prefix - this text goes TO the
            # kid, never to the parent themselves.
            owner_kid_facing_name = row["owner_kid_facing_role"] or row["owner_display_name"]
            body = _generate_creative_reminder_text(row["content"], row["recipient_name"], owner_kid_facing_name)

            # 2026-09-25: send_text_or_template, NOT send_text_message.
            # This path was the one place a proactive message to a kid
            # still had no 24h-window template fallback at all - found
            # while reviewing what else to build, right after the kids'
            # chore reminders were converted from ordinary reminders (which
            # DO have it) to persistent ones (which did not). Since a kid
            # is outside the window by default - a "delivered"/"read"
            # receipt does NOT open it, only a message they actually send
            # does - the nag simply vanished, silently, for exactly the
            # recipients this feature exists for.
            #
            # body_params carries a PLAIN one-line summary, never the
            # creative body itself: WhatsApp rejects template parameters
            # containing newlines, and the creative text always has them
            # (the confirmation instruction is appended on its own line).
            template_content = f"{row['content']} מ-{owner_kid_facing_name}"
            recipient_name = row["recipient_name"]
            owner_number = row["owner_whatsapp_number"]
            sent = send_text_or_template(
                to=row["recipient_whatsapp_number"],
                body=body,
                template_name="reminder_notification",
                language_code=_TEMPLATE_LANGUAGE_CODE,
                body_params=[template_content],
                # Default-arg bound, not closed over - invoked from a later
                # webhook request, long after this loop has moved on (see
                # check_and_send_reminders' own note on the same hazard).
                on_permanent_failure=(
                    lambda r=recipient_name, c=template_content, o=owner_number:
                    notify_reminder_delivery_failure(r, c, o)
                ),
            )
            if not sent:
                # Same reasoning as check_and_send_reminders' synchronous
                # failure branch: tell both parents, and still advance the
                # attempt rather than hot-looping this row every 60s.
                notify_reminder_delivery_failure(recipient_name, template_content, owner_number)
            next_trigger = now_utc + timedelta(minutes=row["retry_interval_minutes"])
            advance_persistent_reminder(row["id"], next_trigger)
        except Exception as e:
            print(f"[scheduler] persistent reminder failed for row {row['id']}: {e}")


def check_and_send_cost_report() -> None:
    """
    New feature (2026-09-27) - the cost-guard the admin asked for before any
    proactive/unattended feature gets built on top of this bot: a real cost
    report every 2 days, plus an immediate alert if month-to-date spend
    crosses COST_ALERT_BUDGET_USD - both sent ONLY to his own number (see
    config.OWNER_WHATSAPP_NUMBER's own docstring for why "only to me", not
    every admin the way check_google_token_health sends to all of them).

    Runs once a day (cron, cheap - reads DB + one BigQuery query, no Gemini
    calls of its own) and decides for itself whether today is actually a
    send day, rather than an interval trigger - an interval anchors to
    whenever the job was first added, which on this actively-developed bot
    (multiple restarts a day during active work) would drift the "every 2
    days" cadence unpredictably. State is persisted in cost_report_state,
    not an in-memory module dict - see that table's own docstring in
    src/db/models.py for the in-memory-cooldown bug this
    specifically avoids repeating (an in-memory cooldown resets on every
    restart, turning "every 2 days" into "every restart" in practice).
    """
    from src.config import COST_ALERT_BUDGET_USD, OWNER_WHATSAPP_NUMBER
    from src.db.models import get_cost_report_state, mark_budget_alert_sent, mark_cost_report_sent
    from src.integrations.gcp_billing import get_month_to_date_cost
    from src.integrations.whatsapp import send_text_message
    from src.webhook_handler import _build_usage_report_text, _estimated_month_cost_usd

    if not OWNER_WHATSAPP_NUMBER:
        return  # not configured - nothing to send, nowhere to send it

    state = get_cost_report_state()
    today = datetime.now(ZoneInfo(DEFAULT_TIMEZONE)).date()

    last_sent = state["last_report_sent_date"]
    due = last_sent is None or (today - date.fromisoformat(last_sent)).days >= 2
    if due:
        try:
            send_text_message(to=OWNER_WHATSAPP_NUMBER, body=_build_usage_report_text())
            mark_cost_report_sent(today.isoformat())
        except Exception as e:
            print(f"[scheduler] cost report send failed (non-fatal): {e}")

    try:
        billing = get_month_to_date_cost()
        month_cost = billing["total"] if billing else _estimated_month_cost_usd()
    except Exception as e:
        print(f"[scheduler] cost guard: could not determine month-to-date cost (non-fatal): {e}")
        return

    if month_cost < COST_ALERT_BUDGET_USD:
        return

    last_alert = state["last_budget_alert_sent_at"]
    if last_alert:
        last_alert_dt = datetime.fromisoformat(last_alert).replace(tzinfo=ZoneInfo("UTC"))
        if datetime.now(ZoneInfo("UTC")) - last_alert_dt < timedelta(hours=24):
            return

    try:
        send_text_message(
            to=OWNER_WHATSAPP_NUMBER,
            body=(
                f"🚨 עברת את תקציב העלות החודשי שקבעת (${COST_ALERT_BUDGET_USD:.2f}) - "
                f"העלות עד כה החודש: ${month_cost:.2f}."
            ),
        )
        mark_budget_alert_sent()
    except Exception as e:
        print(f"[scheduler] budget alert send failed (non-fatal): {e}")


def check_and_monitor_calendar_changes() -> None:
    """
    Context-Aware Gatekeeper, stage 1 (2026-09-27) - the first real
    collector. For every user with proactive_settings.enabled=1, diffs the
    live next-24h Calendar result against calendar_snapshot (the last poll's
    own baseline) to notice a moved, renamed, or cancelled event, or a
    genuinely new one someone else added (e.g. a shared-calendar invite).

    Every detected change goes through proactive.assess_and_deliver - a
    real Gemini judgment call on whether it's actually worth interrupting
    for right now (cross-referencing the user's own calendar and today's
    already-sent count), not just a fixed rule - which itself still applies
    should_deliver_now underneath (quiet hours, an explicit "busy" status,
    the daily cap) regardless of what the judgment call decided. This
    collector never calls send_text_message directly, so it cannot
    accidentally bypass either layer.

    First-run guard: a user's very first poll (calendar_snapshot has no
    rows for them yet) only SEEDS the snapshot, silently - without this,
    activating the feature would report every one of tomorrow's ordinary,
    already-known events as "new", a flood of noise rather than a genuine
    signal. The tradeoff this accepts: on a day where a user's snapshot
    happens to be empty (zero events currently tracked) and a new one is
    then added, that one poll also seeds silently rather than reporting it
    - judged an acceptable, rare edge case rather than worth a dedicated
    "have I ever polled this user" marker.

    Per-user error isolation (12.3) - one user's broken Google connection,
    or one user's diff logic hitting an unexpected error, must never block
    the check for anyone else.
    """
    from src.db.models import (
        admin_list_users,
        delete_calendar_snapshot_event,
        get_calendar_snapshot,
        get_proactive_settings,
        upsert_calendar_snapshot,
    )
    from src.integrations.google_calendar import list_events
    from src.integrations.google_oauth import GoogleAuthExpiredError, NotConnectedError
    from src.proactive import assess_and_deliver

    now_utc = datetime.now(ZoneInfo("UTC"))
    horizon = now_utc + timedelta(hours=24)

    for user in admin_list_users():
        if not user["is_active"]:
            continue
        settings = get_proactive_settings(user["id"])
        if settings is None or not settings["enabled"]:
            continue

        try:
            events = list_events(user["id"], now_utc, horizon, user["timezone"])
        except (NotConnectedError, GoogleAuthExpiredError):
            continue
        except Exception as e:
            print(f"[scheduler] calendar-change monitor: fetch failed for user {user['id']}: {e}")
            continue

        try:
            previous = get_calendar_snapshot(user["id"])
            first_run_for_user = len(previous) == 0
            live_ids = set()

            for event in events:
                event_id = event["id"]
                if not event_id:
                    continue
                live_ids.add(event_id)
                prev = previous.get(event_id)
                if not first_run_for_user:
                    if prev is None:
                        fallback = f"📅 נוסף אירוע חדש ביומן: {event['summary']} ({event['start']})"
                        assess_and_deliver(
                            user, "calendar_new",
                            f"אירוע חדש נוסף ליומן (ייתכן שמישהו אחר הוסיף אותו): "
                            f"\"{event['summary']}\" ב-{event['start']} עד {event['end']}",
                            fallback, calendar_events=events,
                        )
                    elif prev["start"] != event["start"] or prev["end"] != event["end"]:
                        fallback = f"📅 אירוע הוזז: {event['summary']} - עכשיו ב-{event['start']}"
                        assess_and_deliver(
                            user, "calendar_moved",
                            f"אירוע הוזז: \"{event['summary']}\" - היה ב-{prev['start']}, "
                            f"עכשיו ב-{event['start']} עד {event['end']}",
                            fallback, calendar_events=events,
                        )
                    elif prev["summary"] != event["summary"]:
                        fallback = f'📅 אירוע עודכן: "{prev["summary"]}" ← "{event["summary"]}"'
                        assess_and_deliver(
                            user, "calendar_renamed",
                            f'אירוע עודכן: הכותרת השתנתה מ-"{prev["summary"]}" ל-"{event["summary"]}", '
                            f"בשעה {event['start']}",
                            fallback, calendar_events=events,
                        )
                upsert_calendar_snapshot(user["id"], event_id, event["summary"], event["start"], event["end"])

            if not first_run_for_user:
                for event_id, prev in previous.items():
                    if event_id in live_ids:
                        continue
                    # Real bug found live the same day: the 24h lookahead
                    # window is a SLIDING window, not a fixed range - an
                    # event that simply already happened (its own start
                    # time has passed) naturally stops being returned by
                    # list_events once it exits the window, exactly like a
                    # genuinely cancelled one would. Without this check,
                    # every single event would eventually get reported as
                    # "cancelled" purely for having occurred - only an
                    # event whose start was still INSIDE the window (so it
                    # should still have been returned, had it not been
                    # cancelled) is a real cancellation.
                    try:
                        prev_start = datetime.fromisoformat(prev["start"])
                        if prev_start.tzinfo is None:
                            prev_start = prev_start.replace(tzinfo=ZoneInfo("UTC"))
                        already_passed = prev_start < now_utc
                    except (ValueError, TypeError):
                        already_passed = False  # unparseable - fail toward reporting, not silently pruning
                    if already_passed:
                        delete_calendar_snapshot_event(user["id"], event_id)  # aged out, not cancelled - prune quietly
                        continue

                    fallback = f"📅 אירוע בוטל: {prev['summary']} ({prev['start']})"
                    assess_and_deliver(
                        user, "calendar_cancelled",
                        f"אירוע בוטל/הוסר מהיומן: \"{prev['summary']}\" שהיה אמור להתקיים ב-{prev['start']}",
                        fallback, calendar_events=events,
                    )
                    delete_calendar_snapshot_event(user["id"], event_id)
        except Exception as e:
            print(f"[scheduler] calendar-change monitor failed for user {user['id']}: {e}")


def check_and_monitor_new_emails() -> None:
    """
    Context-Aware Gatekeeper, stage 2 (2026-09-27) - proactive email
    monitoring. For every proactive_settings.enabled=1 user with Google
    connected: fetches mail from the last day, diffs against
    gmail_seen_messages to find genuinely new ones since the last poll,
    classifies them in ONE batched Gemini call (gmail.classify_new_emails),
    and notifies for the categories worth interrupting for - urgent_vip,
    bill_deadline, meeting_invite. "shipment" is left to the existing
    package tracker (track_package), which already owns that whole flow;
    "other" stays silent by design, same as the plan's own framing.

    Uses list_recent_emails(query="newer_than:1d") rather than Gmail's own
    History API - simpler, and avoids that API's own historyId-invalidation
    recovery path entirely, at the cost of re-querying a day's mail every
    poll instead of a true delta. gmail_seen_messages is what actually
    makes this a "new since last poll" diff, not the query itself.

    First-run guard (has_any_gmail_seen_message): a user's very first poll
    after enabling proactive mode only SEEDS gmail_seen_messages, silently
    - without this, opting in would immediately report every email from
    the last 24h as if it just arrived, the same flood
    check_and_monitor_calendar_changes's own first-run guard avoids for
    calendar events.

    Every classified email goes through proactive.assess_and_deliver - a
    real Gemini judgment call on whether it's actually worth interrupting
    for right now, cross-referencing the user's own calendar (identifier=
    the sender's own email address, so a VIP sender's message can bypass
    quiet hours underneath) - this collector never calls send_text_message
    directly, so it cannot accidentally skip either layer.

    Per-user error isolation (12.3) - one user's broken Google connection,
    or a classification failure, must never block the check for anyone else.
    """
    from email.utils import parseaddr

    from src.db.models import (
        admin_list_users,
        get_proactive_settings,
        has_any_gmail_seen_message,
        is_gmail_message_seen,
        mark_gmail_message_seen,
    )
    from src.integrations.gmail import classify_new_emails
    from src.integrations.gmail import list_recent_emails as gmail_list_recent_emails
    from src.integrations.google_oauth import GoogleAuthExpiredError, NotConnectedError
    from src.proactive import assess_and_deliver

    category_emoji = {"urgent_vip": "🚨", "bill_deadline": "💳", "meeting_invite": "📅"}

    for user in admin_list_users():
        if not user["is_active"]:
            continue
        settings = get_proactive_settings(user["id"])
        if settings is None or not settings["enabled"]:
            continue

        try:
            emails = gmail_list_recent_emails(user["id"], query="newer_than:1d", max_results=20)
        except (NotConnectedError, GoogleAuthExpiredError):
            continue
        except Exception as e:
            print(f"[scheduler] email monitor: fetch failed for user {user['id']}: {e}")
            continue

        try:
            first_run_for_user = not has_any_gmail_seen_message(user["id"])
            new_emails = [e for e in emails if not is_gmail_message_seen(user["id"], e["id"])]
            for e in new_emails:
                # Marked seen up front, before classification - a
                # classification failure below must never cause the same
                # email to be reprocessed (and possibly double-notified)
                # on the next poll.
                mark_gmail_message_seen(user["id"], e["id"])

            if first_run_for_user or not new_emails:
                continue

            classifications = classify_new_emails(new_emails)
        except Exception as e:
            print(f"[scheduler] email monitor: processing failed for user {user['id']}: {e}")
            continue

        # Efficiency fix (2026-09-27): fetch this user's own upcoming
        # calendar ONCE per poll, not once per assessed email -
        # assess_situation reuses it instead of hitting the Calendar API
        # again for every classified email. Only fetched at all when there
        # is at least one classification to actually assess.
        calendar_events = None
        if classifications:
            try:
                from src.integrations.google_calendar import list_events as _list_calendar_events

                now_utc = datetime.now(ZoneInfo("UTC"))
                calendar_events = _list_calendar_events(
                    user["id"], now_utc, now_utc + timedelta(hours=6), user["timezone"],
                )
            except Exception as e:
                print(f"[scheduler] email monitor: calendar cross-reference failed for user {user['id']} (non-fatal): {e}")

        for c in classifications:
            category = c.get("category")
            if category not in category_emoji:
                continue
            email = next((e for e in new_emails if e["id"] == c.get("id")), None)
            if email is None:
                continue
            sender_email = parseaddr(email["from"])[1]
            summary = c.get("summary") or email["subject"]
            fallback = f"{category_emoji[category]} {summary}\n(ממייל: {email['subject']})"
            event_description = (
                f"מייל חדש מ-{sender_email}, נושא: \"{email['subject']}\", סווג כ-{category}. "
                f"תקציר: {summary}"
            )
            try:
                assess_and_deliver(
                    user, f"email_{category}", event_description, fallback, identifier=sender_email,
                    calendar_events=calendar_events,
                )
            except Exception as e:
                print(f"[scheduler] email monitor: delivery failed for user {user['id']}: {e}")


def check_and_send_meeting_prebriefs() -> None:
    """
    Context-Aware Gatekeeper, stage 3 (2026-09-27) - a short heads-up
    shortly before a meeting starts: title, time, and location (when set).
    For every proactive_settings.enabled=1 user with Google connected,
    using each user's OWN configured meeting_lead_time_minutes (default
    15, see manage_proactive_settings' set_meeting_lead_time action).

    Scope note: the original plan also asked for "recent emails related to
    the attendees or subject" folded into the pre-brief - not built here.
    list_events does not currently return attendee emails at all (it only
    formats id/summary/start/end/location), so that enrichment would need
    a real Calendar API + Gmail-integration change of its own, not just
    this job - flagged as a genuine, deliberate scope cut for a later
    pass, not an oversight.

    meeting_prebrief_sent tracks which event ids already got their brief,
    so a poll inside the same lead-time window doesn't resend it every few
    minutes until the meeting actually starts - same "seen before" pattern
    as gmail_seen_messages/calendar_snapshot.

    Every pre-brief goes through proactive.assess_and_deliver - even a
    routine meeting still gets a real judgment call (e.g. skip briefing an
    all-day/no-location placeholder event), not an unconditional send -
    which itself still applies should_deliver_now underneath (quiet hours/
    VIP/cap). Per-user error isolation (12.3).
    """
    from src.db.models import (
        admin_list_users,
        get_proactive_settings,
        is_meeting_prebriefed,
        mark_meeting_prebriefed,
    )
    from src.integrations.google_calendar import list_events
    from src.integrations.google_oauth import GoogleAuthExpiredError, NotConnectedError
    from src.proactive import assess_and_deliver

    now_utc = datetime.now(ZoneInfo("UTC"))

    for user in admin_list_users():
        if not user["is_active"]:
            continue
        settings = get_proactive_settings(user["id"])
        if settings is None or not settings["enabled"]:
            continue

        lead_minutes = settings["meeting_lead_time_minutes"]
        window_end = now_utc + timedelta(minutes=lead_minutes)

        try:
            events = list_events(user["id"], now_utc, window_end, user["timezone"])
        except (NotConnectedError, GoogleAuthExpiredError):
            continue
        except Exception as e:
            print(f"[scheduler] meeting pre-brief: fetch failed for user {user['id']}: {e}")
            continue

        for event in events:
            event_id = event.get("id")
            if not event_id or is_meeting_prebriefed(user["id"], event_id):
                continue

            location_line = f", במיקום {event['location']}" if event.get("location") else ""
            fallback = f"📅 בעוד {lead_minutes} דקות: {event['summary']} ({event['start']}){location_line}"
            event_description = (
                f"פגישה מתחילה בעוד {lead_minutes} דקות: \"{event['summary']}\" בשעה {event['start']}"
                f"{location_line}"
            )
            try:
                assess_and_deliver(
                    user, "meeting_prebrief", event_description, fallback, calendar_events=events,
                )
            except Exception as e:
                print(f"[scheduler] meeting pre-brief: delivery failed for user {user['id']}: {e}")
            finally:
                # Marked sent regardless of whether assess_and_deliver
                # actually interrupted - a "no, not worth it" judgment call
                # (or a quiet-hours block) must not cause this SAME event
                # to be re-considered every poll until it starts.
                mark_meeting_prebriefed(user["id"], event_id)


_DEFERRED_NOTIFICATION_MAX_AGE_HOURS = 24


def check_and_deliver_deferred_notifications() -> None:
    """
    Gatekeeper stage 4 (2026-09-27) - closes the stage-1 known gap: a
    notification blocked by quiet hours or an explicit "busy" status is no
    longer just dropped (see proactive.deliver_proactive_message) - it's
    held and delivered here once the window actually opens. Never
    re-assessed by the LLM again; the original assess_and_deliver call
    already made that judgment once, this job only re-checks the
    deterministic policy (should_deliver_now).

    Multiple deferred messages for the same user are combined into ONE
    WhatsApp send (a bulleted digest) rather than delivered individually -
    catching up on N held updates then only ever costs 1 slot of the daily
    cap, not N, and matches the plan's own framing ("לא תמיד עדכון
    בודד - מתאחדת לסיכום אחד").

    Safety valve: a deferred message older than
    _DEFERRED_NOTIFICATION_MAX_AGE_HOURS is dropped without sending rather
    than delivered stale (e.g. quiet hours misconfigured to span most of
    the day) - logged, not silently discarded.

    Per-user error isolation (12.3).
    """
    from src.db.models import admin_list_users, delete_deferred_notifications, get_deferred_notifications
    from src.proactive import deliver_proactive_message, should_deliver_now

    now_utc = datetime.now(ZoneInfo("UTC"))

    for user in admin_list_users():
        if not user["is_active"]:
            continue
        try:
            deferred = get_deferred_notifications(user["id"])
            if not deferred:
                continue

            stale_ids = []
            fresh = []
            for row in deferred:
                created = datetime.fromisoformat(row["created_at"].replace(" ", "T")).replace(tzinfo=ZoneInfo("UTC"))
                if now_utc - created > timedelta(hours=_DEFERRED_NOTIFICATION_MAX_AGE_HOURS):
                    stale_ids.append(row["id"])
                else:
                    fresh.append(row)
            if stale_ids:
                print(f"[scheduler] dropping {len(stale_ids)} stale deferred notification(s) for user {user['id']}")
                delete_deferred_notifications(stale_ids)
            if not fresh:
                continue

            allowed, _reason = should_deliver_now(user)
            if not allowed:
                continue  # still blocked (e.g. still in quiet hours) - leave deferred for a later poll

            if len(fresh) == 1:
                body = fresh[0]["body"]
            else:
                lines = "\n".join(f"• {row['body']}" for row in fresh)
                body = f"עדכונים שחיכו (הגיע הזמן הפנוי):\n\n{lines}"

            sent = deliver_proactive_message(user, "deferred_digest", body)
            if sent:
                delete_deferred_notifications([row["id"] for row in fresh])
        except Exception as e:
            print(f"[scheduler] deferred notification delivery failed for user {user['id']}: {e}")


_PROACTIVE_DATA_RETENTION_DAYS = 60


def check_and_cleanup_proactive_data() -> None:
    """
    Maintenance fix (2026-09-27, post-build review): gmail_seen_messages
    and proactive_notification_log had no cleanup job - both grow one row
    per email ever seen / notification ever sent, for every proactive-
    enabled user, forever. Not a correctness issue at personal-bot scale,
    but unbounded. Runs once a day; each table's own delete_old_* function
    in src/db/models.py explains why 60 days is safe to prune (neither
    table's rows are read past that age by anything the app itself does).
    meeting_prebrief_sent (added the same day, stage 3) gets the same
    treatment - its rows are only ever relevant for a few minutes.
    """
    from src.db.models import (
        delete_old_gmail_seen_messages,
        delete_old_meeting_prebrief_sent,
        delete_old_proactive_notification_log,
    )

    try:
        removed_emails = delete_old_gmail_seen_messages(_PROACTIVE_DATA_RETENTION_DAYS)
        removed_notifications = delete_old_proactive_notification_log(_PROACTIVE_DATA_RETENTION_DAYS)
        removed_prebriefs = delete_old_meeting_prebrief_sent(_PROACTIVE_DATA_RETENTION_DAYS)
        if removed_emails or removed_notifications or removed_prebriefs:
            print(
                f"[scheduler] proactive data cleanup: removed {removed_emails} old gmail_seen_messages rows, "
                f"{removed_notifications} old proactive_notification_log rows, "
                f"{removed_prebriefs} old meeting_prebrief_sent rows"
            )
    except Exception as e:
        print(f"[scheduler] proactive data cleanup failed (non-fatal): {e}")


def start_scheduler_loop() -> BackgroundScheduler:
    """Starts the background checks. Called once from src.main at startup."""
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(check_and_send_reminders, "interval", seconds=60, id="reminder_check")
    scheduler.add_job(check_and_notify_package_changes, "interval", hours=1, id="package_status_check")
    scheduler.add_job(check_watches, "interval", hours=1, id="watch_check")
    # Fixed daily time rather than an interval, so a restart cannot turn this
    # into a burst of checks. 06:00 UTC is 09:00 Israel time - a reconnect link
    # arrives at a point in the day when it can actually be acted on.
    scheduler.add_job(check_google_token_health, "cron", hour=6, minute=0, id="google_token_health")
    # timezone=Asia/Jerusalem (not a fixed UTC hour like google_token_health
    # above) so this genuinely fires at 20:00 local time year-round - DST
    # would otherwise drift an "evening" reminder by an hour for half the
    # year, which matters more here than it does for a one-off reconnect nudge.
    scheduler.add_job(
        check_and_send_kids_schedule_reminders, "cron",
        hour=20, minute=0, timezone=ZoneInfo(DEFAULT_TIMEZONE), id="kids_schedule_reminder",
    )
    # An interval, not a fixed cron slot (2026-09-17) - each user now has
    # their own configured send time, so the job itself has to poll and
    # compare against each user's own preference (see the function's own
    # docstring). No per-job timezone needed here the way the fixed-hour
    # jobs above need one for DST-safety - "is it time yet" is computed
    # per-user, inside the function, in each user's own timezone.
    scheduler.add_job(
        check_and_send_daily_meetings_summaries, "interval",
        minutes=DAILY_MEETINGS_SUMMARY_CHECK_INTERVAL_MINUTES, id="daily_meetings_summary",
    )
    # Same 60s granularity as reminder_check - fine enough for a 5-minute
    # retry cadence, and reuses an already-proven-safe polling interval
    # rather than introducing a new one.
    scheduler.add_job(check_and_send_persistent_reminders, "interval", seconds=60, id="persistent_reminder_check")
    # Daily at a fixed local hour (not an interval - see the function's own
    # docstring for why), same DST-safe per-job timezone pattern as
    # kids_schedule_reminder. The function itself decides whether today is
    # actually a "every 2 days" send day.
    scheduler.add_job(
        check_and_send_cost_report, "cron",
        hour=8, minute=0, timezone=ZoneInfo(DEFAULT_TIMEZONE), id="cost_report",
    )
    # Context-Aware Gatekeeper stage 1's first real collector - 15 minutes,
    # cheap (read-only Calendar API call per opted-in user) and frequent
    # enough that a moved/cancelled meeting is caught well before it would
    # otherwise surprise someone.
    scheduler.add_job(
        check_and_monitor_calendar_changes, "interval", minutes=15, id="calendar_change_monitor",
    )
    # Context-Aware Gatekeeper stage 2's collector - 5 minutes, matching the
    # plan's own agreed cadence for email checks.
    scheduler.add_job(
        check_and_monitor_new_emails, "interval", minutes=5, id="email_monitor",
    )
    # Context-Aware Gatekeeper stage 3's collector - 2 minutes, tighter
    # than the other collectors on purpose: the user can configure a short
    # meeting_lead_time_minutes (the whole point is a heads-up close to
    # the actual start), and a coarser poll could miss a narrow window
    # entirely for someone who set e.g. a 5-minute lead time.
    scheduler.add_job(
        check_and_send_meeting_prebriefs, "interval", minutes=2, id="meeting_prebrief",
    )
    # Gatekeeper stage 4 - checks whether any held (quiet-hours/busy-status
    # blocked) notification can now go out. 5 minutes - the window opening
    # (end of quiet hours, status clearing) doesn't need second-level
    # precision the way the meeting pre-brief's own short lead time does.
    scheduler.add_job(
        check_and_deliver_deferred_notifications, "interval", minutes=5, id="deferred_notification_delivery",
    )
    # Daily, off-peak-ish local hour - prunes gmail_seen_messages/
    # proactive_notification_log/meeting_prebrief_sent so none grows unbounded.
    scheduler.add_job(
        check_and_cleanup_proactive_data, "cron",
        hour=4, minute=0, timezone=ZoneInfo(DEFAULT_TIMEZONE), id="proactive_data_cleanup",
    )
    scheduler.start()
    return scheduler

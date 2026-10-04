"""
Decision engine for the proactive assistant ("Context-Aware Gatekeeper",
planned 2026-09-27). Two layers, both required before anything reaches the
user:

1. assess_situation/assess_and_deliver - the real "should this interrupt
   the user at all" judgment call, using an LLM as a filtering/decision
   engine that cross-references the user's own calendar and today's
   already-sent count (the admin's own explicit framing, confirmed live the
   same day this was added - see the module docstring history). Every
   collector calls assess_and_deliver, never deliver_proactive_message
   directly, so a genuinely new collector can't skip this judgment step.
2. should_deliver_now/deliver_proactive_message - the deterministic policy
   underneath (quiet hours, an explicit "I'm busy" status, VIP bypass, and
   a hard daily cap) that ALWAYS applies regardless of what the LLM
   decided - a "yes, interrupt" from assess_situation can still be blocked
   by quiet hours. A user who has never touched manage_proactive_settings
   has no settings row at all (opt-in by default, per the admin's own
   requirement) and is always blocked here.

Deliberately does NOT defer a blocked notification to the next free window
yet (the "hold and deliver later" behavior discussed in planning) - stage 1
just skips it. That's a real, known gap, not an oversight; flagged for a
later stage once a real queue is worth building.
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from src.ai import for_user
from src.config import DEFAULT_TIMEZONE, WHATSAPP_TEMPLATE_LANGUAGE
from src.i18n import t
from src.db.models import (
    count_todays_proactive_notifications,
    defer_proactive_message,
    delete_proactive_notification_log_row,
    get_proactive_settings,
    is_vip_sender,
    reserve_proactive_notification_slot,
)

_DEFERRABLE_REASONS = ("quiet_hours", "status_busy")

# Gatekeeper stage 0 (2026-09-28): the one flexible template every proactive
# message falls back to outside the 24h window. Submitted to Meta the same
# day (Utility category, single body param wrapping the full message text).
_PROACTIVE_TEMPLATE_NAME = "proactive_update"
_PROACTIVE_TEMPLATE_LANGUAGE_CODE = WHATSAPP_TEMPLATE_LANGUAGE


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def is_within_quiet_hours(quiet_start: str, quiet_end: str, now_local: datetime) -> bool:
    """
    Handles the overnight case (e.g. 22:30-07:00, start > end) where the
    quiet window wraps past midnight - "within" then means "at/after start
    OR before end", not a plain start <= now < end range.
    """
    start = _parse_hhmm(quiet_start)
    end = _parse_hhmm(quiet_end)
    now_t = now_local.time()
    if start <= end:
        return start <= now_t < end
    return now_t >= start or now_t < end


def should_deliver_now(user: dict, identifier: str | None = None) -> tuple[bool, str | None]:
    """
    Returns (allowed, reason_blocked | None). identifier: the sender's
    email/WhatsApp number if this notification is about something from a
    specific sender (e.g. an urgent email) - checked against this user's
    own VIP list. A VIP bypasses quiet hours and an explicit "busy" status,
    but NOT the daily cap - even a VIP message still counts toward "don't
    overwhelm the phone" (the admin's own framing).

    reason_blocked values: "proactive_disabled", "daily_cap_reached",
    "status_busy", "quiet_hours" - useful for a collector's own logging,
    not shown to the user.
    """
    settings = get_proactive_settings(user["id"])
    if settings is None or not settings["enabled"]:
        return False, "proactive_disabled"

    if count_todays_proactive_notifications(user["id"]) >= settings["daily_cap"]:
        return False, "daily_cap_reached"

    vip = identifier is not None and is_vip_sender(user["id"], identifier)
    if vip:
        return True, None

    if settings["status_quiet_until"]:
        until = datetime.fromisoformat(settings["status_quiet_until"])
        if until.tzinfo is None:
            until = until.replace(tzinfo=ZoneInfo("UTC"))
        if datetime.now(ZoneInfo("UTC")) < until:
            return False, "status_busy"

    tz = ZoneInfo(user["timezone"] or DEFAULT_TIMEZONE)
    now_local = datetime.now(tz)
    if is_within_quiet_hours(settings["quiet_hours_start"], settings["quiet_hours_end"], now_local):
        return False, "quiet_hours"

    return True, None


def deliver_proactive_message(user: dict, category: str, body: str, identifier: str | None = None) -> bool:
    """
    The single entry point collectors should call: checks should_deliver_now,
    reserves a daily-cap slot, sends via WhatsApp if allowed, and releases
    the slot again if the send itself failed. Returns whether it was
    actually sent.

    Defer fix (2026-09-27, closes the stage-1 known gap): a block for
    quiet_hours or status_busy is a TIMING constraint, not a "never" - the
    message is held (defer_proactive_message) instead of dropped, and
    delivered once the window opens (see scheduler.check_and_deliver_
    deferred_notifications, which combines multiple held messages into one
    digest rather than resending each individually). daily_cap_reached and
    proactive_disabled are NOT deferred - the cap is a volume limiter, not
    a timing one (piling up capped messages for the moment the cap resets
    would defeat its own purpose), and "disabled" has no future window to
    wait for at all.

    Race fix (2026-09-27): should_deliver_now's own cap check is a fast,
    non-atomic pre-check (cheap - no write) that rejects the common case
    early. The actual gate is reserve_proactive_notification_slot right
    before sending - a single atomic SQL statement, so two collectors
    racing for the same user's last slot can no longer both get through
    (see that function's own docstring for the full reasoning - the old
    check-then-separately-log pattern had a real TOCTOU gap between
    APScheduler jobs running on independent intervals).

    Template fallback (Gatekeeper stage 0, 2026-09-28): sends via
    send_text_or_template with the "proactive_update" template (submitted
    to Meta the same day, category Utility, single body param) rather than
    send_text_message alone - previously a proactive message to a user
    outside the 24h customer-service window simply failed with nothing to
    fall back to, silently dropping calendar/email updates for anyone who
    hadn't messaged the bot recently. Safe to wire in before Meta's
    approval lands (see send_text_or_template's own docstring): until
    then, the template attempt fails the same way the old plain send did.
    """
    from src.integrations.whatsapp import send_text_or_template

    allowed, reason = should_deliver_now(user, identifier)
    if not allowed:
        if reason in _DEFERRABLE_REASONS:
            defer_proactive_message(user["id"], category, body, identifier)
            print(f"[proactive] deferred for user {user['id']} ({category}): {reason}")
        else:
            print(f"[proactive] blocked for user {user['id']} ({category}): {reason}")
        return False

    settings = get_proactive_settings(user["id"])
    cap = settings["daily_cap"] if settings else 0
    row_id = reserve_proactive_notification_slot(user["id"], cap, category, body[:200])
    if row_id is None:
        print(f"[proactive] blocked for user {user['id']} ({category}): daily_cap_reached (race)")
        return False

    sent = send_text_or_template(
        to=user["whatsapp_number"], body=body,
        template_name=_PROACTIVE_TEMPLATE_NAME, language_code=_PROACTIVE_TEMPLATE_LANGUAGE_CODE,
        body_params=[body],
    )
    if not sent:
        delete_proactive_notification_log_row(row_id)  # don't waste a slot on a failed attempt
    return sent


@for_user
def assess_situation(
    user: dict, event_description: str, category: str, calendar_events: list[dict] | None = None,
) -> dict | None:
    """
    The real "situation assessment" layer (2026-09-27, added same day
    after the admin asked to confirm this was actually built, not just the
    surrounding plumbing) - his own framing: use the LLM as a filtering/
    decision engine that runs in the background, cross-references data,
    and decides IF, WHEN and HOW to interrupt. Not a fixed category-based
    rule - one Gemini call per candidate event (never per poll; only
    called once a collector already has something worth considering),
    given the user's own upcoming calendar and how many proactive
    notifications already went out today.

    calendar_events (efficiency fix, 2026-09-27): pass the caller's OWN
    already-fetched events when it has them (both collectors do - the
    calendar monitor already pulled its 24h window, and the email monitor
    can fetch once per poll and reuse across every email it assesses)
    instead of this function hitting the Calendar API again per call - a
    poll with N candidate events used to cost N+1 Calendar API calls
    instead of 1. Only fetches internally (the original behavior) when the
    caller has nothing to hand it.

    Returns {"interrupt": bool, "message": str}, or None on a call
    failure - see assess_and_deliver for the fallback that failure gets.
    """
    from src.integrations.gemini import call_gemini_json

    tz = ZoneInfo(user["timezone"] or DEFAULT_TIMEZONE)
    now_local = datetime.now(tz)

    if calendar_events is not None:
        calendar_text = (
            "\n".join(f"- {e['start']}: {e['summary']}" for e in calendar_events)
            if calendar_events else t("assess.calendar_empty")
        )
    else:
        calendar_text = t("assess.calendar_unavailable")
        try:
            from src.integrations.google_calendar import list_events

            now_utc = datetime.now(ZoneInfo("UTC"))
            upcoming = list_events(user["id"], now_utc, now_utc + timedelta(hours=6), user["timezone"])
            calendar_text = (
                "\n".join(f"- {e['start']}: {e['summary']}" for e in upcoming)
                if upcoming else t("assess.calendar_empty_6h")
            )
        except Exception:
            pass

    settings = get_proactive_settings(user["id"])
    already_sent = count_todays_proactive_notifications(user["id"])
    cap = settings["daily_cap"] if settings else None

    # Real bug found live the same day this shipped: a genuinely urgent
    # "pick up your kid today, there's a problem" email (category
    # urgent_vip - already a real filter classify_new_emails applies, not
    # a raw guess) got interrupt=False, because the prompt below treated
    # every category identically under a caution-first default ("if in
    # doubt, don't interrupt"). That default is backwards for a category
    # that was already screened as genuinely urgent one step earlier - the
    # question at THIS layer for those categories is "is there a real
    # reason to hold back" (e.g. it's stale, already handled, or plainly
    # not time-sensitive despite the label), not "is there a real reason
    # to send it." Categories that were never pre-screened for urgency
    # (bill_deadline, meeting_invite, calendar_new/renamed) keep the
    # original cautious default - those really can often wait.
    high_priority_categories = ("urgent_vip", "calendar_cancelled", "calendar_moved")
    if category in high_priority_categories:
        default_bias = t("assess.bias_priority")
    else:
        default_bias = t("assess.bias_cautious")

    # Real bug found live the same day, right after the category-bias fix
    # above: with cap=None (settings row missing) the old code defaulted
    # to 0, producing "already sent 0 out of a maximum of 0" - which reads
    # as "the quota is already full", and suppressed an urgent event for
    # exactly that reason. Now: omit the line entirely when the cap isn't
    # known, and explicitly say when there's plenty of room left so a
    # non-issue doesn't get treated as a soft signal to hold back.
    if cap is None:
        cap_line = ""
    elif already_sent >= cap:
        cap_line = t("assess.cap_full", sent=already_sent, cap=cap)
    elif cap - already_sent <= 1:
        cap_line = t("assess.cap_low", sent=already_sent, cap=cap)
    else:
        cap_line = t("assess.cap_ok", sent=already_sent, cap=cap)

    # Security review, 2026-09-27: event_description and calendar_text are
    # ultimately derived from external content (an email's subject/snippet,
    # or a calendar event someone else could have created/invited to) -
    # for email, one hop removed through classify_new_emails' own summary,
    # but that summary is itself Gemini output, not sanitized input, so a
    # prompt-injection attempt could in principle survive into it. Neither
    # was explicitly fenced as untrusted here before this review (unlike
    # classify_new_emails' own prompt, and _FORWARDED_SUGGESTION_PREAMBLE) -
    # added defense in depth even though the practical blast radius is
    # small (this call can only decide interrupt true/false and phrase a
    # WhatsApp text, never take an action on its own).
    prompt = t(
        "assess.prompt", now=now_local.strftime("%Y-%m-%d %H:%M (%A)"), calendar=calendar_text,
        cap_line=cap_line, category=category, description=event_description, bias=default_bias,
    )
    result = call_gemini_json(prompt)
    if not result:
        return None
    return {"interrupt": bool(result.get("interrupt")), "message": result.get("message") or event_description}


def assess_and_deliver(
    user: dict, category: str, event_description: str, fallback_body: str, identifier: str | None = None,
    calendar_events: list[dict] | None = None,
) -> bool:
    """
    The entry point collectors call now instead of deliver_proactive_message
    directly: runs the event past assess_situation first, and only
    delivers if the assessment decides it's actually worth interrupting
    for - deliver_proactive_message still applies the deterministic policy
    (quiet hours/VIP/cap) underneath, unchanged.

    calendar_events: passed straight through to assess_situation - see its
    own docstring for why (avoids a redundant Calendar API call per event
    when the caller already has one fetch to reuse).

    On an assessment call failure, falls back to fallback_body (the
    collector's own plain factual message) rather than silently dropping a
    real event - the same "boring but arrives beats silent" logic
    scheduler._generate_creative_reminder_text's own Gemini-failure
    fallback already uses, for the same reason.
    """
    try:
        assessment = assess_situation(user, event_description, category, calendar_events)
    except Exception as e:
        print(f"[proactive] situation assessment failed for user {user['id']} ({category}): {e}")
        assessment = None

    if assessment is None:
        return deliver_proactive_message(user, category, fallback_body, identifier)
    if not assessment["interrupt"]:
        print(f"[proactive] assessment decided NOT to interrupt user {user['id']} ({category})")
        return False
    return deliver_proactive_message(user, category, assessment["message"], identifier)

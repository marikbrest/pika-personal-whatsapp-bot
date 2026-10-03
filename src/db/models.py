"""
SQLite connection and schema loading.
See PRD.md section 12.3 - WAL mode + busy_timeout to avoid "SQLite locked"
when the webhook handler and the scheduler read/write concurrently.
"""
import sqlite3
from datetime import datetime
import threading
from pathlib import Path

from src.config import DEFAULT_TIMEZONE, DB_PATH

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _inserted_id(cur: sqlite3.Cursor) -> int:
    """cursor.lastrowid is typed Optional; after a successful INSERT it is always set."""
    if cur.lastrowid is None:
        raise RuntimeError("INSERT did not return a row id")
    return cur.lastrowid


def get_connection() -> sqlite3.Connection:
    """
    Returns a properly configured SQLite connection:
    - WAL mode: allows better concurrent reads/writes (PRD 12.3)
    - busy_timeout: waits instead of failing immediately if the DB is briefly locked
    - row_factory: allows accessing columns by name (row["col"])
    """
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=10000;")  # 10 seconds
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Creates all tables from schema.sql if they do not exist yet."""
    conn = get_connection()
    try:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.commit()
        _run_migrations(conn)
    finally:
        conn.close()


def _run_migrations(conn: sqlite3.Connection) -> None:
    """
    Small migrations for an existing DB created before a column/table was added
    to schema.sql. CREATE TABLE IF NOT EXISTS does not help with new columns on
    an existing table, so we check and add them manually. Safe to run on every
    startup (a no-op if the column already exists).
    """
    cols = [row["name"] for row in conn.execute("PRAGMA table_info(reminders)")]
    if "recipient_contact_id" not in cols:
        conn.execute("ALTER TABLE reminders ADD COLUMN recipient_contact_id INTEGER REFERENCES contacts(id)")
        conn.commit()

    user_cols = [row["name"] for row in conn.execute("PRAGMA table_info(users)")]
    if "is_active" not in user_cols:
        # Lets the dashboard disable a user without deleting their data
        conn.execute("ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT 1")
        conn.commit()

    if "welcome_sent_at" not in user_cols:
        # Set once the privacy/terms welcome message (src/welcome.py) was delivered, so it is sent only once.
        conn.execute("ALTER TABLE users ADD COLUMN welcome_sent_at TIMESTAMP")
        conn.commit()

    if "is_admin" not in user_cols:
        # Only an admin may manage users through chat (see _handle_user_manage).
        # The default of 0 is deliberate - granting admin is an explicit action,
        # never automatic.
        conn.execute("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0")
        conn.commit()

    if "daily_meetings_summary_enabled" not in user_cols:
        # New feature (2026-09-15): opt-in daily calendar summary (see
        # scheduler.check_and_send_daily_meetings_summaries). Default 0 -
        # morning_brief.py's own docstring already documents the deliberate
        # "never scheduled or pushed automatically" design for the on-demand
        # brief; this is a genuinely separate, explicitly opt-in feature, not
        # a reversal of that decision.
        conn.execute("ALTER TABLE users ADD COLUMN daily_meetings_summary_enabled BOOLEAN DEFAULT 0")
        conn.commit()

    if "daily_meetings_summary_time" not in user_cols:
        # 2026-09-17: per-user configurable send time (was a single fixed
        # 07:00 for everyone) - the admin asked for this after the other
        # parent wanted 9:00 and the bot could only offer a flat 7:00. 'HH:MM' 24h, in the
        # user's own timezone (users.timezone). SQLite backfills this
        # DEFAULT onto existing rows too, not just new ones.
        conn.execute("ALTER TABLE users ADD COLUMN daily_meetings_summary_time TEXT DEFAULT '07:00'")
        conn.commit()

    if "daily_meetings_summary_last_sent_date" not in user_cols:
        # Dedup marker (this user's own local 'YYYY-MM-DD') so the frequent
        # interval-based check (scheduler.check_and_send_daily_meetings_
        # summaries, now polling every few minutes instead of a single fixed
        # daily cron slot - needed once the time itself became per-user)
        # doesn't re-send once the target time has already passed today.
        conn.execute("ALTER TABLE users ADD COLUMN daily_meetings_summary_last_sent_date TEXT")
        conn.commit()

    if "notify_on_reminder_delivery_failure" not in user_cols:
        # New feature: after a 24h-window delivery bug (a reminder to a kid
        # could silently never arrive while the code believed it had
        # succeeded), the admin asked for BOTH parents to hear about it
        # whenever a reminder to a kid genuinely, finally fails to deliver -
        # not just whichever parent happened to create that specific
        # reminder. A flag rather than a hardcoded pair of phone numbers, so
        # it stays correct if the family's parent set ever changes. Default
        # 0 for everyone - this is a per-family setting, so a self-hosted
        # deployment should turn it on for its own parent accounts (e.g. via
        # a one-off `UPDATE users SET notify_on_reminder_delivery_failure =
        # 1 WHERE whatsapp_number IN (...)`), not something this schema
        # should assume on anyone's behalf.
        conn.execute("ALTER TABLE users ADD COLUMN notify_on_reminder_delivery_failure BOOLEAN DEFAULT 0")
        conn.commit()

    if "kid_facing_role" not in user_cols:
        # New feature: the admin asked for a split - when the bot sends a
        # message TO one of the kids that names a parent (a reminder's
        # "מ-X" prefix, the creative nag text), it should say "אבא"/"אימא"
        # (Dad/Mom), but whenever the bot addresses that parent directly it
        # should keep using their own name (display_name, unchanged). So
        # this is a SEPARATE field from display_name, not a replacement for
        # it - see scheduler.check_and_send_reminders/check_and_send_
        # persistent_reminders, which use kid_facing_role (falling back to
        # display_name if unset) specifically when composing kid-facing
        # text, and display_name everywhere else, unchanged. NULL for
        # everyone by default - a self-hosted deployment should set this
        # per-parent for its own family (e.g. `UPDATE users SET
        # kid_facing_role = 'Dad' WHERE whatsapp_number = '...'`).
        conn.execute("ALTER TABLE users ADD COLUMN kid_facing_role TEXT")
        conn.commit()

    message_cols = [row["name"] for row in conn.execute("PRAGMA table_info(messages)")]
    if "embedding" not in message_cols:
        # For semantic search (see save_incoming_message) - only populated for
        # substantive incoming text messages, not every row.
        conn.execute("ALTER TABLE messages ADD COLUMN embedding BLOB")
        conn.commit()

    tables = [row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    if "tracked_packages" not in tables:
        # Package tracking (see webhook_handler._handle_package_status) - no
        # ship24_tracker_id column, since Ship24's per-call API plan is
        # stateless (POST /tracking/search returns results directly, no
        # persistent tracker to register first).
        conn.execute(
            """
            CREATE TABLE tracked_packages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                tracking_number TEXT NOT NULL,
                courier_code TEXT,
                description TEXT,
                source_email_id TEXT,
                last_status TEXT,
                last_checked_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, tracking_number)
            )
            """
        )
        conn.commit()

    if "intent_shadow_log" not in tables:
        # Stage B of the function-calling migration: every text message the
        # OLD classifier already handled is also run through the new tools
        # pipeline in the background (src.tools.shadow), purely to compare
        # verdicts - never acted on, never sent to anyone. No foreign key to
        # messages.id: save_incoming_message doesn't expose the row id it
        # inserts, and storing whatsapp_message_id + raw_content directly
        # here is simpler and makes the eventual agreement report
        # self-contained without a join.
        conn.execute(
            """
            CREATE TABLE intent_shadow_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                whatsapp_message_id TEXT,
                raw_content TEXT,
                old_intent TEXT NOT NULL,
                new_tool TEXT,
                new_args TEXT,
                error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()

    if "api_usage_log" not in tables:
        # Real token/cost + external-API-quota tracking (see
        # webhook_handler._handle_usage_status, admin-only). provider is one
        # of 'gemini_generate' | 'gemini_embed' | 'ship24'. input_tokens/
        # output_tokens are exact counts for gemini_generate (from the API's
        # own usage_metadata), an estimate for gemini_embed (the embedding
        # API exposes no usage metadata at all - confirmed empirically, not
        # documented), and both null for ship24 (a call is a call, no tokens).
        conn.execute(
            """
            CREATE TABLE api_usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                input_tokens INTEGER,
                output_tokens INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()

    if "watches" not in tables:
        # Generic watch/trigger engine - the same "poll periodically, notify
        # only on change" pattern check_and_notify_package_changes has always
        # used, generalized to any watchable target instead of being
        # package-tracking-specific. watch_type selects which checker
        # function in src.integrations.watchers runs (currently
        # 'email_reply' | 'web_page'); target is that checker's input (a
        # Gmail thread_id or a URL); last_state is whatever comparable string
        # that checker returns - opaque to this table, meaningful only to the
        # checker that wrote it. last_state starts NULL so the first check
        # after creation always just establishes a baseline, never a false
        # "it changed" notification (same reasoning as tracked_packages'
        # last_status being NULL on insert).
        conn.execute(
            """
            CREATE TABLE watches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                watch_type TEXT NOT NULL,
                target TEXT NOT NULL,
                label TEXT,
                last_state TEXT,
                is_active BOOLEAN DEFAULT 1,
                last_checked_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()

    if "pending_suggestions" not in tables:
        # Batch 9 (2026-09-14): proactive suggestions from a forwarded message
        # (see webhook_handler._suggest_action_from_forwarded). args_json is the
        # exact arguments a real tool from the registry was classified with -
        # stored verbatim, not re-derived, so confirming later executes exactly
        # what was proposed, not a fresh (possibly different) re-classification.
        # Same "at most one active row per user, ownership-checked on every
        # write" shape as email_drafts, for the same reason: this is a
        # confirm-before-you-act gate, and no code path should be able to
        # confirm or dismiss another user's pending suggestion.
        conn.execute(
            """
            CREATE TABLE pending_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                tool_name TEXT NOT NULL,
                args_json TEXT NOT NULL,
                confirmation_text TEXT NOT NULL,
                source_content TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP
            )
            """
        )
        conn.commit()

    if "kids_schedule" not in tables:
        # New feature (2026-09-15): a recurring weekly timetable per kid, so
        # the bot can proactively remind the parent each evening what
        # tomorrow looks like (see scheduler.check_and_send_kids_schedule_
        # reminders). One row per (kid, weekday) - day_of_week reuses the
        # same 3-letter lowercase convention as reminders.schedule_days
        # (mon/tue/wed/thu/fri/sat/sun), not a fresh format.
        conn.execute(
            """
            CREATE TABLE kids_schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                kid_name TEXT NOT NULL,
                day_of_week TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, kid_name, day_of_week)
            )
            """
        )
        conn.commit()

    if "persistent_reminders" not in tables:
        # New feature (2026-09-17): a reminder that keeps re-nagging its
        # recipient every retry_interval_minutes until they explicitly
        # confirm they did it (see webhook_handler._check_task_confirmation
        # and scheduler.check_and_send_persistent_reminders), instead of
        # firing once like an ordinary reminders row. the admin asked for this
        # specifically for the kids: "לא יפספסו... לא יפסיק עד שהם יאשרו".
        # recipient_contact_id (not a plain phone number) reuses the same
        # contacts table every other reminder-to-a-contact feature already
        # uses - either parent can create one for the same kid,
        # since each has their own contact row for them.
        conn.execute(
            """
            CREATE TABLE persistent_reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_user_id INTEGER NOT NULL REFERENCES users(id),
                recipient_contact_id INTEGER NOT NULL REFERENCES contacts(id),
                content TEXT NOT NULL,
                retry_interval_minutes INTEGER NOT NULL DEFAULT 5,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                attempts_sent INTEGER NOT NULL DEFAULT 0,
                next_trigger_at TIMESTAMP NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP
            )
            """
        )
        conn.commit()

    persistent_reminder_cols = [row["name"] for row in conn.execute("PRAGMA table_info(persistent_reminders)")]
    if "schedule_type" not in persistent_reminder_cols:
        # 2026-09-17 same-day addition: a persistent reminder can now
        # recur (daily/weekly), not just fire once - the admin asked for "כל
        # יום בערב" / "כל יום א,ב,ג" support, same as ordinary reminders
        # already have. Reuses the exact same schedule_type/schedule_time/
        # schedule_days convention as reminders.schedule_type (once uses a
        # full ISO datetime in schedule_time; daily/weekly use 'HH:MM' +
        # schedule_days for weekly) - see
        # webhook_handler._handle_persistent_reminders and
        # scheduler.check_and_send_persistent_reminders for how a
        # recurring row resets itself (attempts_sent back to 0, a freshly
        # computed next_trigger_at) instead of terminally resolving once
        # confirmed or escalated.
        conn.execute("ALTER TABLE persistent_reminders ADD COLUMN schedule_type TEXT NOT NULL DEFAULT 'once'")
        conn.execute("ALTER TABLE persistent_reminders ADD COLUMN schedule_time TEXT")
        conn.execute("ALTER TABLE persistent_reminders ADD COLUMN schedule_days TEXT")
        conn.commit()

    if "proactive_settings" not in tables:
        # Context-Aware Gatekeeper, stage 1 (2026-09-27) - the delivery
        # policy every proactive collector must go through before sending
        # anything (see src/proactive.should_deliver_now). One row per
        # user, created on first "manage_proactive_settings" call - opt-in
        # by default (enabled=0), per the admin's own requirement that this
        # never activates for a user without being asked. quiet_hours_*
        # default to his own agreed 22:30-07:00; status_quiet_until is the
        # explicit "אני בפגישה עד 16:00" override (see
        # webhook_handler._handle_manage_proactive_settings) - NULL when
        # nothing was set, and checked/cleared automatically once it's in
        # the past.
        conn.execute(
            """
            CREATE TABLE proactive_settings (
                user_id INTEGER PRIMARY KEY REFERENCES users(id),
                enabled BOOLEAN NOT NULL DEFAULT 0,
                quiet_hours_start TEXT NOT NULL DEFAULT '22:30',
                quiet_hours_end TEXT NOT NULL DEFAULT '07:00',
                daily_cap INTEGER NOT NULL DEFAULT 6,
                status_quiet_until TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()

    proactive_settings_cols = [row["name"] for row in conn.execute("PRAGMA table_info(proactive_settings)")]
    if "meeting_lead_time_minutes" not in proactive_settings_cols:
        # Gatekeeper stage 3 (2026-09-27): how long before a meeting starts
        # to send the pre-brief (scheduler.check_and_send_meeting_prebriefs)
        # - the admin's own agreed default of 15 minutes during planning.
        conn.execute(
            "ALTER TABLE proactive_settings ADD COLUMN meeting_lead_time_minutes INTEGER NOT NULL DEFAULT 15"
        )
        conn.commit()

    if "vip_senders" not in tables:
        # Gatekeeper stage 1: a sender (email or WhatsApp number) that
        # bypasses quiet hours and an explicit "busy" status, but NOT the
        # daily cap - even a VIP message still counts toward "don't
        # overwhelm the phone" (see proactive.should_deliver_now). Owner-
        # scoped like contacts/saved_links - each user's VIP list is their
        # own, not shared with anyone else's.
        conn.execute(
            """
            CREATE TABLE vip_senders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_user_id INTEGER NOT NULL REFERENCES users(id),
                identifier TEXT NOT NULL,
                label TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(owner_user_id, identifier)
            )
            """
        )
        conn.commit()

    if "proactive_notification_log" not in tables:
        # Gatekeeper stage 1: every proactive send that actually went out,
        # for the daily-cap count (see
        # count_todays_proactive_notifications) - counts only real sends,
        # never ones should_deliver_now blocked.
        conn.execute(
            """
            CREATE TABLE proactive_notification_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                category TEXT NOT NULL,
                summary TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()

    if "calendar_snapshot" not in tables:
        # Gatekeeper stage 1, first real collector: calendar-change
        # detection (scheduler.check_and_monitor_calendar_changes). One row
        # per (user, event) the collector has seen before in the next-24h
        # window - a later poll diffs the live Calendar API result against
        # this snapshot to notice a moved/cancelled event, or a genuinely
        # new one someone else added (e.g. a shared-calendar invite), and
        # updates its own row's summary/start/end/last_seen_at either way.
        conn.execute(
            """
            CREATE TABLE calendar_snapshot (
                user_id INTEGER NOT NULL REFERENCES users(id),
                event_id TEXT NOT NULL,
                summary TEXT,
                start TEXT,
                end TEXT,
                last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, event_id)
            )
            """
        )
        conn.commit()

    if "gmail_seen_messages" not in tables:
        # Context-Aware Gatekeeper stage 2 (2026-09-27) - proactive email
        # monitoring (scheduler.check_and_monitor_new_emails). Every message
        # id the collector has ever seen for this user, so a later poll can
        # tell a genuinely NEW email (since last poll) apart from one
        # that's merely "recent" (list_recent_emails is queried by
        # newer_than:1d, not by an actual delta cursor - simpler and more
        # robust than Gmail's History API, which needs its own
        # historyId-invalidation recovery path this avoids entirely). Same
        # first-run-seed-silently pattern as calendar_snapshot - see
        # has_any_gmail_seen_message's own docstring.
        conn.execute(
            """
            CREATE TABLE gmail_seen_messages (
                user_id INTEGER NOT NULL REFERENCES users(id),
                message_id TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, message_id)
            )
            """
        )
        conn.commit()

    if "meeting_prebrief_sent" not in tables:
        # Gatekeeper stage 3 (2026-09-27) - which upcoming events already
        # got their pre-brief sent (scheduler.check_and_send_meeting_
        # prebriefs), so a poll inside the same lead-time window doesn't
        # resend it every few minutes until the meeting actually starts.
        # Rows are short-lived by nature (an event's own lead-time window
        # is measured in minutes) - pruned by the same daily cleanup job as
        # gmail_seen_messages/proactive_notification_log.
        conn.execute(
            """
            CREATE TABLE meeting_prebrief_sent (
                user_id INTEGER NOT NULL REFERENCES users(id),
                event_id TEXT NOT NULL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, event_id)
            )
            """
        )
        conn.commit()

    if "deferred_proactive_notifications" not in tables:
        # Gatekeeper stage 4 (2026-09-27) - closes the stage-1 known gap: a
        # notification blocked by quiet hours or an explicit "busy" status
        # is held here instead of just dropped, and delivered once the
        # window opens (scheduler.check_and_deliver_deferred_notifications)
        # - never re-assessed by the LLM again, the original
        # assess_and_deliver call already made that judgment once.
        # Deliberately NOT used for a daily-cap block - that's a volume
        # limiter, not a timing constraint (see deliver_proactive_message's
        # own docstring for why piling those up would defeat the cap's
        # purpose). identifier is kept for potential future use (e.g.
        # re-checking VIP status) but the current delivery job does not
        # need it - multiple deferred rows for one user are combined into
        # a single digest, not delivered per-original-sender.
        conn.execute(
            """
            CREATE TABLE deferred_proactive_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                category TEXT NOT NULL,
                body TEXT NOT NULL,
                identifier TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()

    if "cost_report_state" not in tables:
        # New feature (2026-09-27) - the cost-guard the admin asked for before
        # any proactive (unattended, self-triggering) feature gets built:
        # a report every 2 days plus an immediate alert if month-to-date
        # spend crosses COST_ALERT_BUDGET_USD, both sent only to his own
        # number (see scheduler.check_and_send_cost_report). Single-row
        # table (id fixed at 1) - there is only ever one owner to track
        # this for, not a per-user thing. Persisted here rather than an
        # in-memory module dict deliberately: an in-memory alert
        # cooldown in another project showed exactly
        # what goes wrong otherwise - an in-memory cooldown resets on every
        # restart/deploy, which on this actively-developed bot happens
        # multiple times a day, turning a "every 2 days" cadence into
        # "every restart" in practice.
        conn.execute(
            """
            CREATE TABLE cost_report_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_report_sent_date TEXT,
                last_budget_alert_sent_at TIMESTAMP
            )
            """
        )
        conn.execute("INSERT INTO cost_report_state (id) VALUES (1)")
        conn.commit()

    if "admin_audit_log" not in tables:
        # New feature (2026-09-26): the admin asked for a real, provable answer
        # to "do you see my messages" - every admin action that touches
        # another user's data (viewing their reminders, cancelling one,
        # adding/disabling a user account, or opening the raw process log,
        # which can carry any user's content) gets a row here, written from
        # src/admin_handler.py at the moment it happens. admin_email (not a
        # users.id FK) is the identity Cloudflare Access itself verified for
        # that request - the actual authenticated party, independent of
        # whether that email happens to also be tied to a `users` row.
        conn.execute(
            """
            CREATE TABLE admin_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_email TEXT NOT NULL,
                action TEXT NOT NULL,
                target_user_id INTEGER REFERENCES users(id),
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()

    if "pending_image_uploads" not in tables:
        # New feature (2026-09-26): editing a photo the user sends (see
        # webhook_handler._handle_edit_image / src/tools/batch15.py's
        # edit_image tool). Stores only the WhatsApp media_id, never the raw
        # image bytes - the handler re-downloads via download_media(media_id)
        # at edit time, same "don't put binary blobs in SQLite" choice this
        # codebase already made for every other media type. One row per user
        # (user_id itself is the primary key, not an autoincrement id) - a
        # newly uploaded image simply replaces whatever was pending before,
        # since there is never a reason to edit two different photos at once.
        conn.execute(
            """
            CREATE TABLE pending_image_uploads (
                user_id INTEGER PRIMARY KEY REFERENCES users(id),
                media_id TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


def get_user_by_whatsapp_number(whatsapp_number: str) -> sqlite3.Row | None:
    """
    Looks up an existing user by WhatsApp number.
    PRD 14.2 - allowlist: users are never created automatically. An unknown
    number returns None and the webhook handler silently ignores the message.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT * FROM users WHERE whatsapp_number = ? AND is_active = 1", (whatsapp_number,)
        )
        return cur.fetchone()
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> sqlite3.Row | None:
    """Looks up a user by id - used when arriving from an OAuth callback state
    rather than from a WhatsApp number."""
    conn = get_connection()
    try:
        cur = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        return cur.fetchone()
    finally:
        conn.close()


def message_exists(whatsapp_message_id: str) -> bool:
    """
    Idempotency check (PRD 12.1) - whether a message with this ID was already
    processed. Meta may deliver the same webhook more than once.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT 1 FROM messages WHERE whatsapp_message_id = ?",
            (whatsapp_message_id,),
        )
        return cur.fetchone() is not None
    finally:
        conn.close()


MIN_EMBEDDABLE_LENGTH = 15


def _embed_message_in_background(message_id: int, raw_content: str) -> None:
    """
    Runs on a background thread (see save_incoming_message) so the embedding
    API call never delays the user's reply - measured at ~330-470ms per call,
    which was previously paid synchronously by every substantive incoming
    message before the bot could even respond. A UPDATE after the fact is
    fine since semantic search only ever reads rows with embedding IS NOT
    NULL - a brief window where a very recent message isn't yet searchable
    is a non-issue.
    """
    try:
        from src.integrations.embeddings import embed_text
        embedding = embed_text(raw_content)
    except Exception as e:
        print(f"[models] background embedding failed (non-fatal): {e}")
        return

    conn = get_connection()
    try:
        conn.execute("UPDATE messages SET embedding = ? WHERE id = ?", (embedding, message_id))
        conn.commit()
    finally:
        conn.close()


def save_incoming_message(
    user_id: int, raw_content: str, whatsapp_message_id: str, message_type: str = "text",
    parsed_intent: str | None = None,
) -> None:
    """
    Saves an incoming message. Relies on UNIQUE(whatsapp_message_id) as an
    additional safety net.

    parsed_intent: the classifier's own verdict for this message (Gemini's
    "intent" field, or a fixed label like "media_unsupported" for the
    early-return paths that never reach the classifier at all). The column
    has existed in schema.sql since the very first commit but was never
    actually written until now - this is step 0 of the function-calling
    migration: a regression baseline needs real logged classifications to
    compare against, and there were none. See scripts/build_intent_baseline.py
    for the one-time backfill over messages that predate this change.

    Also embeds the message for semantic search, but only when it's a
    substantive text message (message_type == "text" and longer than
    MIN_EMBEDDABLE_LENGTH) - skips "כן"/"תודה"-style noise that has no search
    value and would only add embedding-API cost for nothing. The embedding
    call itself runs on a background thread (_embed_message_in_background)
    so it never adds to the user's perceived response time; the row is
    inserted with embedding=NULL immediately and updated shortly after.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO messages (user_id, direction, message_type, raw_content, whatsapp_message_id, parsed_intent)
            VALUES (?, 'incoming', ?, ?, ?, ?)
            """,
            (user_id, message_type, raw_content, whatsapp_message_id, parsed_intent),
        )
        conn.commit()
        message_id = cur.lastrowid
    except sqlite3.IntegrityError:
        # Duplicate message (Meta delivered the webhook twice) - not a real error, just ignore
        return
    finally:
        conn.close()

    if message_type == "text" and raw_content and len(raw_content) >= MIN_EMBEDDABLE_LENGTH:
        threading.Thread(
            target=_embed_message_in_background, args=(message_id, raw_content), daemon=True
        ).start()


def save_outgoing_message(user_id: int, raw_content: str) -> None:
    """
    Saves a reply the bot sent. Needed so that the conversation history passed
    to Gemini contains both sides, not just the user's messages.
    Outgoing messages have no whatsapp_message_id, so there is no UNIQUE conflict.
    """
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO messages (user_id, direction, message_type, raw_content)
            VALUES (?, 'outgoing', 'text', ?)
            """,
            (user_id, raw_content),
        )
        conn.commit()
    finally:
        conn.close()


def get_recent_messages(user_id: int, limit: int = 10, within_hours: int = 24) -> list[sqlite3.Row]:
    """
    Returns the most recent messages in the conversation (both directions),
    oldest first, to be fed to Gemini as context.

    Bounded both by count (limit) and by time (within_hours) - a conversation
    from two days ago is no longer relevant context, and including it only
    inflates token cost.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT direction, raw_content FROM messages
            WHERE user_id = ?
              AND created_at >= datetime('now', ?)
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, f"-{int(within_hours)} hours", limit),
        )
        rows = cur.fetchall()
        return list(reversed(rows))  # oldest first, as a conversational model expects
    finally:
        conn.close()


def get_user_data_summary(user_id: int) -> dict:
    """
    New feature (2026-09-26): the "show me what you have on me" self-service
    privacy tool (see webhook_handler._handle_manage_my_data / src/tools/
    batch16.py's manage_my_data tool) - one query per data category, all
    scoped to this single user_id, never touching anything belonging to
    anyone else. Counts only, not the content itself - the point is showing
    WHAT categories of data exist and roughly how much, not re-dumping the
    whole conversation back at the user (which get_recent_messages/chat
    already lets them ask for separately if they actually want that).
    """
    conn = get_connection()
    try:
        message_row = conn.execute(
            "SELECT COUNT(*) AS c, MIN(created_at) AS first_at, MAX(created_at) AS last_at "
            "FROM messages WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        reminders_count = conn.execute(
            "SELECT COUNT(*) AS c FROM reminders WHERE user_id = ? AND is_active = 1", (user_id,)
        ).fetchone()["c"]
        persistent_count = conn.execute(
            "SELECT COUNT(*) AS c FROM persistent_reminders WHERE owner_user_id = ? AND status = 'pending'",
            (user_id,),
        ).fetchone()["c"]
        tasks_count = conn.execute(
            "SELECT COUNT(*) AS c FROM tasks WHERE user_id = ? AND is_done = 0", (user_id,)
        ).fetchone()["c"]
        contacts_count = conn.execute(
            "SELECT COUNT(*) AS c FROM contacts WHERE owner_user_id = ?", (user_id,)
        ).fetchone()["c"]
        saved_links_count = conn.execute(
            "SELECT COUNT(*) AS c FROM saved_links WHERE user_id = ?", (user_id,)
        ).fetchone()["c"]
        facts_count = conn.execute(
            "SELECT COUNT(*) AS c FROM user_profile_facts WHERE user_id = ?", (user_id,)
        ).fetchone()["c"]
        google_connected = conn.execute(
            "SELECT 1 FROM oauth_tokens WHERE user_id = ? AND provider = 'google'", (user_id,)
        ).fetchone() is not None

        return {
            "message_count": message_row["c"],
            "first_message_at": message_row["first_at"],
            "last_message_at": message_row["last_at"],
            "active_reminders": reminders_count,
            "active_persistent_reminders": persistent_count,
            "open_tasks": tasks_count,
            "contacts": contacts_count,
            "saved_links": saved_links_count,
            "remembered_facts": facts_count,
            "google_connected": google_connected,
        }
    finally:
        conn.close()


def delete_all_messages_for_user(user_id: int) -> int:
    """
    Deletes this user's own conversation history (both directions) - the
    "delete my history" half of the self-service privacy tool. Scoped to
    user_id like every other write in this file; deletes ONLY the messages
    table, not reminders/tasks/contacts/facts/saved_links, since "delete my
    history" was asked for specifically as the conversation log, not a
    full-account wipe. Returns how many rows were removed.
    """
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def save_contact(owner_user_id: int, name: str, whatsapp_number: str) -> int:
    """
    Saves or updates one of the user's contacts (upsert by name - if "Mom"
    already exists, the number is updated rather than creating a duplicate).
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO contacts (owner_user_id, name, whatsapp_number)
            VALUES (?, ?, ?)
            ON CONFLICT(owner_user_id, name) DO UPDATE SET whatsapp_number = excluded.whatsapp_number
            """,
            (owner_user_id, name, whatsapp_number),
        )
        conn.commit()
        return _inserted_id(cur)
    finally:
        conn.close()


def get_contact_by_name(owner_user_id: int, name: str) -> sqlite3.Row | None:
    """Looks up a contact by name (case-insensitive), for family reminders."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT * FROM contacts WHERE owner_user_id = ? AND LOWER(name) = LOWER(?)",
            (owner_user_id, name),
        )
        return cur.fetchone()
    finally:
        conn.close()


def list_contacts(owner_user_id: int) -> list[sqlite3.Row]:
    """All of the user's contacts - fed into the prompt so Gemini can match names."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT name, whatsapp_number FROM contacts WHERE owner_user_id = ? ORDER BY name",
            (owner_user_id,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def save_reminder(
    user_id: int,
    content: str,
    schedule_type: str,
    schedule_time: str,
    schedule_days: str | None,
    next_trigger_at: datetime,  # already computed by src.scheduler.compute_next_trigger
    recipient_contact_id: int | None = None,
) -> int:
    """
    Saves a new reminder and returns its id.
    recipient_contact_id: when empty this is an ordinary reminder for the user
    themselves; when set, the reminder is actually delivered to that contact
    (see get_due_reminders).
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO reminders (user_id, content, schedule_type, schedule_time, schedule_days,
                                    next_trigger_at, recipient_contact_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id, content, schedule_type, schedule_time, schedule_days,
                next_trigger_at.isoformat(), recipient_contact_id,
            ),
        )
        conn.commit()
        return _inserted_id(cur)
    finally:
        conn.close()


def get_due_reminders(now_utc_iso: str) -> list[sqlite3.Row]:
    """
    Returns every active reminder that is due (next_trigger_at <= now).

    destination_number is the number actually sent to: the contact's number when
    the reminder was created for someone else (recipient_contact_id), otherwise
    the user's own number. recipient_name is only populated when there is a
    different recipient, so the message can be worded accordingly.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT r.id, r.user_id, r.content, r.schedule_type, r.schedule_time, r.schedule_days,
                   u.timezone,
                   u.display_name AS owner_display_name,
                   u.kid_facing_role AS owner_kid_facing_role,
                   u.whatsapp_number AS owner_number,
                   COALESCE(c.whatsapp_number, u.whatsapp_number) AS destination_number,
                   c.name AS recipient_name
            FROM reminders r
            JOIN users u ON u.id = r.user_id
            LEFT JOIN contacts c ON c.id = r.recipient_contact_id
            WHERE r.is_active = 1 AND r.next_trigger_at <= ?
            ORDER BY r.user_id, r.next_trigger_at
            """,
            (now_utc_iso,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def list_active_reminders(user_id: int) -> list[sqlite3.Row]:
    """
    All of the user's active reminders (including ones they created for
    contacts), ordered by next_trigger_at. Backs both the "what do I have
    scheduled" listing and reminder cancellation.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT r.id, r.content, r.schedule_type, r.schedule_time, r.schedule_days,
                   r.next_trigger_at, c.name AS recipient_name
            FROM reminders r
            LEFT JOIN contacts c ON c.id = r.recipient_contact_id
            WHERE r.user_id = ? AND r.is_active = 1
            ORDER BY r.next_trigger_at
            """,
            (user_id,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def deactivate_reminder(reminder_id: int, user_id: int | None = None) -> bool:
    """
    Deactivates a reminder. Returns True if a row was actually updated.

    user_id: when supplied, the reminder is only deactivated if it genuinely
    belongs to that user. This is defence in depth against IDOR - even if future
    code passes in an id that came from user input without an ownership check,
    it cannot cancel someone else's reminder.
    The scheduler calls this without user_id (it operates on reminders it
    fetched itself).
    """
    conn = get_connection()
    try:
        if user_id is None:
            cur = conn.execute("UPDATE reminders SET is_active = 0 WHERE id = ?", (reminder_id,))
        else:
            cur = conn.execute(
                "UPDATE reminders SET is_active = 0 WHERE id = ? AND user_id = ?",
                (reminder_id, user_id),
            )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_reminder_next_trigger(reminder_id: int, next_trigger_at: datetime) -> None:
    """Updates next_trigger_at for a recurring reminder (daily/weekly) after it was sent."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE reminders SET next_trigger_at = ? WHERE id = ?",
            (next_trigger_at.isoformat(), reminder_id),
        )
        conn.commit()
    finally:
        conn.close()


def upsert_oauth_tokens(
    user_id: int,
    provider: str,
    access_token_encrypted: str,
    refresh_token_encrypted: str,
    scope: str,
    expires_at: datetime | None,
) -> None:
    """
    Saves or updates OAuth tokens (Google etc.) for a user+provider. There is
    deliberately no DB-level UNIQUE constraint on (user_id, provider) - the check
    is done here (SELECT then UPDATE/INSERT) to avoid requiring a migration on a
    table that already exists in production.
    """
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM oauth_tokens WHERE user_id = ? AND provider = ?",
            (user_id, provider),
        ).fetchone()

        expires_at_str = expires_at.isoformat() if expires_at else None

        if existing:
            conn.execute(
                """
                UPDATE oauth_tokens
                SET access_token_encrypted = ?, refresh_token_encrypted = ?, scope = ?, expires_at = ?
                WHERE id = ?
                """,
                (access_token_encrypted, refresh_token_encrypted, scope, expires_at_str, existing["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO oauth_tokens (user_id, provider, access_token_encrypted, refresh_token_encrypted, scope, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, provider, access_token_encrypted, refresh_token_encrypted, scope, expires_at_str),
            )
        conn.commit()
    finally:
        conn.close()


def get_oauth_tokens(user_id: int, provider: str) -> sqlite3.Row | None:
    """Returns the encrypted token row for a user+provider, or None if not connected yet."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT * FROM oauth_tokens WHERE user_id = ? AND provider = ?",
            (user_id, provider),
        )
        return cur.fetchone()
    finally:
        conn.close()


def save_email_draft(user_id: int, to_address: str, subject: str, body: str) -> int:
    """Saves a new email draft awaiting approval. Returns its id."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO email_drafts (user_id, to_address, subject, body, status)
            VALUES (?, ?, ?, ?, 'pending_approval')
            """,
            (user_id, to_address, subject, body),
        )
        conn.commit()
        return _inserted_id(cur)
    finally:
        conn.close()


def get_pending_draft(user_id: int) -> sqlite3.Row | None:
    """
    Returns the user's most recent email draft awaiting approval, if any.
    There is at most one "active" draft at a time - a new draft is not created
    while one is already pending (enforced by the logic in webhook_handler).
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT * FROM email_drafts
            WHERE user_id = ? AND status = 'pending_approval'
            ORDER BY id DESC LIMIT 1
            """,
            (user_id,),
        )
        return cur.fetchone()
    finally:
        conn.close()


def update_draft_status(draft_id: int, status: str, user_id: int) -> bool:
    """
    Updates a draft's status: 'sent', 'cancelled' etc.
    user_id is mandatory - an email draft is among the most sensitive data here,
    and no code path should be able to send or cancel another user's draft.
    Returns True if a row was updated.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE email_drafts SET status = ?, approved_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
            (status, draft_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_draft_body(draft_id: int, new_subject: str, new_body: str, user_id: int) -> bool:
    """Updates draft content after an edit request. Ownership-checked, like update_draft_status."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE email_drafts SET subject = ?, body = ? WHERE id = ? AND user_id = ?",
            (new_subject, new_body, draft_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def save_pending_suggestion(
    user_id: int, tool_name: str, args_json: str, confirmation_text: str, source_content: str,
) -> int:
    """
    Saves a proposed action from a forwarded message, awaiting the user's
    confirmation (see webhook_handler._suggest_action_from_forwarded). Returns
    its id. Callers are expected to check get_pending_suggestion first and not
    create a second one while one is already pending - same discipline as
    save_email_draft/get_pending_draft.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO pending_suggestions
                (user_id, tool_name, args_json, confirmation_text, source_content, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
            """,
            (user_id, tool_name, args_json, confirmation_text, source_content),
        )
        conn.commit()
        return _inserted_id(cur)
    finally:
        conn.close()


PENDING_SUGGESTION_TTL_HOURS = 2


def get_pending_suggestion(user_id: int) -> sqlite3.Row | None:
    """
    Returns the user's most recent suggestion awaiting confirmation, if any -
    unless it is older than PENDING_SUGGESTION_TTL_HOURS, in which case it is
    marked 'expired' here (not silently skipped - an explicit status, same as
    'confirmed'/'dismissed', so it stays visible in the data) and None is
    returned instead. Without this, a suggestion from a forward the user
    never replied to could sit pending indefinitely, and a much later,
    completely unrelated "כן" in some other conversation could end up
    confirming it - stale content triggering a real action days after the
    fact. Uses SQLite's own datetime('now', ...) rather than parsing
    created_at in Python, the same style get_usage_summary already uses for
    "today"/"this month" - avoids any manual timezone/format handling.
    """
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE pending_suggestions SET status = 'expired', resolved_at = CURRENT_TIMESTAMP
            WHERE user_id = ? AND status = 'pending'
              AND created_at < datetime('now', ?)
            """,
            (user_id, f"-{PENDING_SUGGESTION_TTL_HOURS} hours"),
        )
        conn.commit()

        cur = conn.execute(
            "SELECT * FROM pending_suggestions WHERE user_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
            (user_id,),
        )
        return cur.fetchone()
    finally:
        conn.close()


def update_suggestion_status(suggestion_id: int, status: str, user_id: int) -> bool:
    """
    Updates a suggestion's status: 'confirmed', 'dismissed'. user_id is
    mandatory - same IDOR defence as update_draft_status, since this gates a
    real tool execution (confirming one runs whatever action was proposed).
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE pending_suggestions SET status = ?, resolved_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
            (status, suggestion_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


PENDING_IMAGE_UPLOAD_TTL_MINUTES = 15


def save_pending_image_upload(user_id: int, media_id: str, mime_type: str) -> None:
    """
    Records the most recently uploaded image as available for editing (see
    webhook_handler._handle_edit_image) - INSERT OR REPLACE, since only the
    single most recent upload is ever relevant (see the table's own
    docstring in _run_migrations).
    """
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO pending_image_uploads (user_id, media_id, mime_type, created_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (user_id, media_id, mime_type),
        )
        conn.commit()
    finally:
        conn.close()


def get_pending_image_upload(user_id: int) -> sqlite3.Row | None:
    """
    Returns the user's most recently uploaded image, if any and if not older
    than PENDING_IMAGE_UPLOAD_TTL_MINUTES - same reasoning as
    get_pending_suggestion's own TTL: without one, a photo sent long ago
    could still be "edited" by an unrelated later message. An expired row is
    deleted outright here (not marked, unlike pending_suggestions) - there is
    no status history worth keeping for this table, just "is there one right
    now or not."
    """
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM pending_image_uploads WHERE user_id = ? AND created_at < datetime('now', ?)",
            (user_id, f"-{PENDING_IMAGE_UPLOAD_TTL_MINUTES} minutes"),
        )
        conn.commit()
        cur = conn.execute("SELECT * FROM pending_image_uploads WHERE user_id = ?", (user_id,))
        return cur.fetchone()
    finally:
        conn.close()


def clear_pending_image_upload(user_id: int) -> None:
    """Called once an edit has actually been produced - see
    webhook_handler._handle_edit_image's own docstring for why chaining
    further edits onto the result is deliberately not supported yet (the
    user would need to send/attach the edited image again to keep editing)."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM pending_image_uploads WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


# ===== Functions for the admin dashboard (admin_handler.py) =====


def log_admin_action(admin_email: str, action: str, target_user_id: int | None = None, details: str | None = None) -> None:
    """Records one admin_audit_log row - see its own docstring in _run_migrations
    for why admin_email, not a users.id FK. Never raises to the caller (a
    logging failure must not block the real admin action it's documenting)."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO admin_audit_log (admin_email, action, target_user_id, details) VALUES (?, ?, ?, ?)",
            (admin_email, action, target_user_id, details),
        )
        conn.commit()
    finally:
        conn.close()


def list_admin_audit_log(limit: int = 200) -> list[sqlite3.Row]:
    """Most recent admin actions first, with the target user's display name
    joined in for readability (NULL for actions with no specific target,
    e.g. viewing the raw log file)."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT a.id, a.admin_email, a.action, a.target_user_id, a.details, a.created_at,
                   u.display_name AS target_display_name
            FROM admin_audit_log a
            LEFT JOIN users u ON u.id = a.target_user_id
            ORDER BY a.id DESC LIMIT ?
            """,
            (limit,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def admin_list_users() -> list[sqlite3.Row]:
    """All users including disabled ones, with message and active-reminder counts."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT u.id, u.whatsapp_number, u.display_name, u.timezone, u.is_active, u.is_admin, u.created_at,
                   (SELECT COUNT(*) FROM messages m WHERE m.user_id = u.id) AS message_count,
                   (SELECT COUNT(*) FROM reminders r WHERE r.user_id = u.id AND r.is_active = 1) AS active_reminders,
                   (SELECT MAX(m.created_at) FROM messages m WHERE m.user_id = u.id) AS last_message_at
            FROM users u ORDER BY u.id
            """
        )
        return cur.fetchall()
    finally:
        conn.close()


def admin_add_user(whatsapp_number: str, display_name: str) -> bool:
    """Adds a new user to the allowlist. Returns False if the number already exists."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO users (whatsapp_number, display_name, timezone) VALUES (?, ?, ?)",
            (whatsapp_number, display_name, DEFAULT_TIMEZONE),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def admin_set_user_active(user_id: int, is_active: bool) -> None:
    """Enables/disables a user. Disabled means the bot ignores them; their data is kept."""
    conn = get_connection()
    try:
        conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if is_active else 0, user_id))
        conn.commit()
    finally:
        conn.close()


def admin_message_stats(days: int = 7) -> list[sqlite3.Row]:
    """Messages per day (both directions) for the last N days, for the dashboard chart."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT DATE(created_at) AS day, direction, COUNT(*) AS cnt
            FROM messages
            WHERE created_at >= datetime('now', ?)
            GROUP BY DATE(created_at), direction
            ORDER BY day
            """,
            (f"-{int(days)} days",),
        )
        return cur.fetchall()
    finally:
        conn.close()


def admin_list_user_reminders(user_id: int) -> list[sqlite3.Row]:
    """A specific user's active reminders - for viewing/cancelling from the dashboard."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT r.id, r.content, r.schedule_type, r.schedule_time, r.schedule_days,
                   r.next_trigger_at, c.name AS recipient_name
            FROM reminders r
            LEFT JOIN contacts c ON c.id = r.recipient_contact_id
            WHERE r.user_id = ? AND r.is_active = 1
            ORDER BY r.next_trigger_at
            """,
            (user_id,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def admin_get_user(user_id: int) -> sqlite3.Row | None:
    """A single user by id, including disabled ones (unlike get_user_by_id, which the bot uses)."""
    conn = get_connection()
    try:
        cur = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        return cur.fetchone()
    finally:
        conn.close()


def admin_message_totals() -> dict[str, int]:
    """Total messages in the system by direction - used to estimate Gemini cost."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT direction, COUNT(*) AS cnt FROM messages GROUP BY direction"
        )
        return {r["direction"]: r["cnt"] for r in cur.fetchall()}
    finally:
        conn.close()


def get_user_by_number_any_status(whatsapp_number: str) -> sqlite3.Row | None:
    """
    Like get_user_by_whatsapp_number but *including* disabled users.
    Needed for user management, to distinguish "this number does not exist" from
    "it exists but is disabled" (in which case it should be re-enabled rather
    than recreated).
    """
    conn = get_connection()
    try:
        cur = conn.execute("SELECT * FROM users WHERE whatsapp_number = ?", (whatsapp_number,))
        return cur.fetchone()
    finally:
        conn.close()


# ===== Long-term memory (user_profile_facts) =====
#
# Facts are only ever stored because the user explicitly asked for them to be
# remembered. There is deliberately no automatic extraction: a wrongly inferred
# fact would be injected into every future prompt and silently skew answers
# forever, which is far worse than having no fact at all.

MAX_FACTS_PER_USER = 50


def save_user_fact(user_id: int, fact_key: str, fact_value: str) -> bool:
    """
    Stores or updates a single remembered fact. Returns False if the user is at
    MAX_FACTS_PER_USER and this would be a new key (updating an existing key is
    always allowed).

    The cap exists because every fact is injected into every prompt: unbounded
    growth would steadily increase both latency and token cost on every message.
    """
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM user_profile_facts WHERE user_id = ? AND fact_key = ?",
            (user_id, fact_key),
        ).fetchone()

        if existing is None:
            count = conn.execute(
                "SELECT COUNT(*) AS c FROM user_profile_facts WHERE user_id = ?", (user_id,)
            ).fetchone()["c"]
            if count >= MAX_FACTS_PER_USER:
                return False

        conn.execute(
            """
            INSERT INTO user_profile_facts (user_id, fact_key, fact_value, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, fact_key)
            DO UPDATE SET fact_value = excluded.fact_value, updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, fact_key, fact_value),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def list_user_facts(user_id: int) -> list[sqlite3.Row]:
    """All facts remembered about a user, oldest first. Fed into the prompt as context."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT id, fact_key, fact_value, updated_at
            FROM user_profile_facts WHERE user_id = ?
            ORDER BY updated_at
            """,
            (user_id,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def delete_user_fact(user_id: int, fact_key: str) -> bool:
    """
    Deletes one fact by key. Scoped by user_id so no code path can delete
    another user's memory. Returns True if a row was actually removed.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM user_profile_facts WHERE user_id = ? AND fact_key = ?",
            (user_id, fact_key),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_all_user_facts(user_id: int) -> int:
    """Deletes every fact for a user. Returns how many were removed."""
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM user_profile_facts WHERE user_id = ?", (user_id,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def update_reminder_schedule(
    reminder_id: int,
    user_id: int,
    schedule_type: str,
    schedule_time: str,
    schedule_days: str | None,
    next_trigger_at: datetime,
) -> bool:
    """
    Rewrites a reminder's schedule (used when the user reschedules or snoozes).
    Ownership-checked like the other ID-based mutations. Returns True if updated.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            UPDATE reminders
            SET schedule_type = ?, schedule_time = ?, schedule_days = ?, next_trigger_at = ?
            WHERE id = ? AND user_id = ? AND is_active = 1
            """,
            (schedule_type, schedule_time, schedule_days, next_trigger_at.isoformat(),
             reminder_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_reminder_content(reminder_id: int, user_id: int, content: str) -> bool:
    """Rewrites a reminder's text. Ownership-checked. Returns True if updated."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE reminders SET content = ? WHERE id = ? AND user_id = ? AND is_active = 1",
            (content, reminder_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_last_sent_reminder(user_id: int) -> sqlite3.Row | None:
    """
    The user's most recently triggered reminder that is still active - the one a
    bare "snooze an hour" almost certainly refers to. Recurring reminders keep
    their next_trigger_at in the future after firing, so this looks at the most
    recently updated active reminder rather than at trigger time alone.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT id, content, schedule_type, schedule_time, schedule_days, next_trigger_at
            FROM reminders
            WHERE user_id = ? AND is_active = 1
            ORDER BY next_trigger_at
            LIMIT 1
            """,
            (user_id,),
        )
        return cur.fetchone()
    finally:
        conn.close()


def save_link(
    user_id: int,
    original_url: str,
    title: str | None,
    content_snapshot: str | None,
    content_type: str | None,
    fetch_status: str,
    embedding: bytes | None,
) -> int:
    """Stores a saved link with its permanent content snapshot (PRD founding
    principle #1 - a copy of the content, not just the URL)."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO saved_links
                (user_id, original_url, title, content_snapshot, content_type, fetch_status, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, original_url, title, content_snapshot, content_type, fetch_status, embedding),
        )
        conn.commit()
        return _inserted_id(cur)
    finally:
        conn.close()


def list_saved_links(user_id: int, limit: int = 20) -> list[sqlite3.Row]:
    """The user's saved links, newest first."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT id, original_url, title, fetch_status, created_at
            FROM saved_links WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return cur.fetchall()
    finally:
        conn.close()


def find_saved_link_by_match(user_id: int, match: str) -> sqlite3.Row | None:
    """
    Finds a saved link by a substring of its title or URL - same
    disambiguation style as reminder_manage's match_content. Returns the
    single match, or None if zero or more than one link matches (the caller
    should ask the user to be more specific in the latter case).
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT id, original_url, title FROM saved_links
            WHERE user_id = ? AND (title LIKE ? OR original_url LIKE ?)
            """,
            (user_id, f"%{match}%", f"%{match}%"),
        )
        rows = cur.fetchall()
        return rows[0] if len(rows) == 1 else None
    finally:
        conn.close()


def delete_saved_link(user_id: int, link_id: int) -> bool:
    """Deletes one saved link. Ownership-checked. Returns True if removed."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM saved_links WHERE id = ? AND user_id = ?", (link_id, user_id)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_saved_links_with_embeddings(user_id: int) -> list[sqlite3.Row]:
    """Every saved link that has a stored embedding, for semantic search."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT id, original_url, title, content_snapshot, embedding
            FROM saved_links WHERE user_id = ? AND embedding IS NOT NULL
            """,
            (user_id,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def get_messages_with_embeddings(user_id: int, limit: int = 500) -> list[sqlite3.Row]:
    """
    This user's most recent embedded messages, for semantic search. Bounded by
    limit since brute-force cosine similarity is done in Python - fine at
    personal scale, but unbounded growth would slow every search down over
    months of use.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT raw_content, embedding, created_at FROM messages
            WHERE user_id = ? AND embedding IS NOT NULL
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return cur.fetchall()
    finally:
        conn.close()


def get_processed_email_ids(user_id: int) -> set[str]:
    """Gmail message ids already scanned for tracking info, so the same email
    is never re-processed (saves a Gemini call every time)."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "SELECT source_email_id FROM tracked_packages WHERE user_id = ? AND source_email_id IS NOT NULL",
            (user_id,),
        )
        return {row["source_email_id"] for row in cur.fetchall()}
    finally:
        conn.close()


def add_tracked_package(
    user_id: int,
    tracking_number: str,
    courier_code: str | None,
    description: str | None,
    source_email_id: str | None,
) -> None:
    """
    Adds a newly-found package. source_email_id is None when the user gave
    the tracking number directly in chat rather than it being found via
    Gmail. UNIQUE(user_id, tracking_number) means a duplicate insert (same
    tracking number found again some other way) is silently ignored rather
    than erroring.
    """
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO tracked_packages
                (user_id, tracking_number, courier_code, description, source_email_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, tracking_number, courier_code, description, source_email_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_tracked_packages(user_id: int) -> list[sqlite3.Row]:
    """This user's tracked packages, for _handle_package_status to re-check."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT id, tracking_number, courier_code, description, last_status, last_checked_at
            FROM tracked_packages WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def get_packages_due_for_check(cutoff_iso: str) -> list[sqlite3.Row]:
    """
    Every tracked package (across all users) not checked since cutoff_iso (or
    never checked), joined with the owning user's whatsapp_number/timezone
    for notification. Used by scheduler.check_and_notify_package_changes,
    called hourly - passing (now - 24h) as cutoff_iso naturally staggers each
    package to a once-a-day check from whenever it was first found/last
    checked, not a fixed global clock time. Terminal-status filtering
    (delivered/etc) is done by the caller, not here, to keep that list in one
    place (scheduler.py) - as is the give-up rule for packages that never
    register with any carrier, which needs created_at (selected here).
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT tp.id, tp.user_id, tp.tracking_number, tp.courier_code, tp.description,
                   tp.last_status, tp.last_checked_at, tp.created_at,
                   u.whatsapp_number, u.timezone
            FROM tracked_packages tp
            JOIN users u ON u.id = tp.user_id
            WHERE tp.last_checked_at IS NULL OR tp.last_checked_at <= ?
            """,
            (cutoff_iso,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def update_package_status(package_id: int, status: str) -> None:
    """Records the latest known status and check time - last_checked_at is
    what the hourly re-check throttle in webhook_handler reads."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE tracked_packages SET last_status = ?, last_checked_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, package_id),
        )
        conn.commit()
    finally:
        conn.close()


def add_watch(user_id: int, watch_type: str, target: str, label: str | None) -> int:
    """
    Registers a new watch. last_state is left NULL - the first scheduled
    check establishes the baseline, exactly like a freshly tracked package's
    last_status. Returns the new row's id.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO watches (user_id, watch_type, target, label) VALUES (?, ?, ?, ?)",
            (user_id, watch_type, target, label),
        )
        conn.commit()
        return _inserted_id(cur)
    finally:
        conn.close()


def list_active_watches(user_id: int) -> list[sqlite3.Row]:
    """This user's active watches, for the 'what am I watching' listing and
    for matching a cancel request against."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT id, watch_type, target, label, last_state, created_at
            FROM watches WHERE user_id = ? AND is_active = 1
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def get_watches_due_for_check(cutoff_iso: str) -> list[sqlite3.Row]:
    """
    Every active watch (across all users) not checked since cutoff_iso (or
    never checked), joined with the owning user's whatsapp_number for
    notification - same shape and staggering behaviour as
    get_packages_due_for_check.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT w.id, w.user_id, w.watch_type, w.target, w.label, w.last_state,
                   u.whatsapp_number
            FROM watches w
            JOIN users u ON u.id = w.user_id
            WHERE w.is_active = 1 AND (w.last_checked_at IS NULL OR w.last_checked_at <= ?)
            """,
            (cutoff_iso,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def update_watch_state(watch_id: int, new_state: str) -> None:
    """Records the latest known state and check time."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE watches SET last_state = ?, last_checked_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_state, watch_id),
        )
        conn.commit()
    finally:
        conn.close()


def deactivate_watch(watch_id: int, user_id: int | None = None) -> bool:
    """
    Deactivates a watch. Returns True if a row was actually updated.
    user_id, when supplied, scopes the update to that owner - same IDOR
    defence-in-depth as deactivate_reminder.
    """
    conn = get_connection()
    try:
        if user_id is None:
            cur = conn.execute("UPDATE watches SET is_active = 0 WHERE id = ?", (watch_id,))
        else:
            cur = conn.execute(
                "UPDATE watches SET is_active = 0 WHERE id = ? AND user_id = ?", (watch_id, user_id)
            )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


MAX_OPEN_TASKS_PER_USER = 100


def add_task(user_id: int, content: str, list_name: str = "default") -> bool:
    """
    Adds one item to a user's list. Returns False when the user already has
    MAX_OPEN_TASKS_PER_USER open items - a cap in the same spirit as
    MAX_FACTS_PER_USER: an unbounded list is usually the symptom of a
    misclassification loop, and growing forever makes that harder to notice.
    """
    conn = get_connection()
    try:
        open_count = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE user_id = ? AND is_done = 0", (user_id,)
        ).fetchone()[0]
        if open_count >= MAX_OPEN_TASKS_PER_USER:
            return False
        conn.execute(
            "INSERT INTO tasks (user_id, list_name, content) VALUES (?, ?, ?)",
            (user_id, list_name, content),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def list_tasks(user_id: int, list_name: str | None = None, include_done: bool = False) -> list[sqlite3.Row]:
    """
    A user's items, oldest first. list_name=None spans every list they have,
    which is what "what's on my list" should do when no list was named.
    """
    conn = get_connection()
    try:
        sql = "SELECT id, list_name, content, is_done, created_at FROM tasks WHERE user_id = ?"
        params: list = [user_id]
        if list_name:
            sql += " AND list_name = ?"
            params.append(list_name)
        if not include_done:
            sql += " AND is_done = 0"
        sql += " ORDER BY id"
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def set_task_done(user_id: int, task_id: int) -> bool:
    """
    Marks one item done. user_id is in the WHERE clause deliberately, not just
    checked beforehand - same IDOR defence as deactivate_reminder.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE tasks SET is_done = 1, completed_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND user_id = ? AND is_done = 0",
            (task_id, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_task(user_id: int, task_id: int) -> bool:
    """Removes one item. Ownership-scoped in the WHERE clause (see set_task_done)."""
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def clear_tasks(user_id: int, list_name: str | None = None) -> int:
    """Empties a list (or every list when list_name is None). Returns how many went."""
    conn = get_connection()
    try:
        if list_name:
            cur = conn.execute(
                "DELETE FROM tasks WHERE user_id = ? AND list_name = ?", (user_id, list_name)
            )
        else:
            cur = conn.execute("DELETE FROM tasks WHERE user_id = ?", (user_id,))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


MAX_KIDS_SCHEDULE_ROWS_PER_USER = 70  # generous - e.g. up to 10 kids x 7 days, same spirit as MAX_OPEN_TASKS_PER_USER


def upsert_kid_schedule_day(user_id: int, kid_name: str, day_of_week: str, content: str) -> bool:
    """
    Sets (or replaces) one kid's schedule for one day of the week. Returns
    False only when this would be a genuinely NEW row and the user is
    already at MAX_KIDS_SCHEDULE_ROWS_PER_USER - editing an existing
    (user_id, kid_name, day_of_week) row is always allowed regardless of the
    cap, since it doesn't grow the table.
    """
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT 1 FROM kids_schedule WHERE user_id = ? AND kid_name = ? AND day_of_week = ?",
            (user_id, kid_name, day_of_week),
        ).fetchone()
        if existing is None:
            count = conn.execute(
                "SELECT COUNT(*) FROM kids_schedule WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
            if count >= MAX_KIDS_SCHEDULE_ROWS_PER_USER:
                return False
        conn.execute(
            """
            INSERT INTO kids_schedule (user_id, kid_name, day_of_week, content)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, kid_name, day_of_week)
            DO UPDATE SET content = excluded.content, updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, kid_name, day_of_week, content),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_kid_schedule(user_id: int, kid_name: str | None = None) -> list[sqlite3.Row]:
    """
    All saved schedule days for a user, optionally scoped to one kid. Order
    is by kid_name then insertion (id) - a caller that needs weekday order
    (see webhook_handler._handle_kids_schedule) sorts in Python against
    scheduler._WEEKDAY_NAMES, since expressing "order by this specific list"
    in SQLite needs a CASE expression this doesn't bother with.
    """
    conn = get_connection()
    try:
        sql = "SELECT id, kid_name, day_of_week, content FROM kids_schedule WHERE user_id = ?"
        params: list = [user_id]
        if kid_name:
            sql += " AND kid_name = ?"
            params.append(kid_name)
        sql += " ORDER BY kid_name, id"
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def delete_kid_schedule_day(user_id: int, kid_name: str, day_of_week: str | None = None) -> int:
    """Removes one day (or the kid's entire schedule when day_of_week is
    None). Returns how many rows went, so the caller can say "nothing to
    delete" accurately instead of always claiming success."""
    conn = get_connection()
    try:
        if day_of_week:
            cur = conn.execute(
                "DELETE FROM kids_schedule WHERE user_id = ? AND kid_name = ? AND day_of_week = ?",
                (user_id, kid_name, day_of_week),
            )
        else:
            cur = conn.execute(
                "DELETE FROM kids_schedule WHERE user_id = ? AND kid_name = ?", (user_id, kid_name)
            )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def get_kids_schedule_for_day(user_id: int, day_of_week: str) -> list[sqlite3.Row]:
    """Every kid's content for one specific weekday - what
    scheduler.check_and_send_kids_schedule_reminders sends each evening."""
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT kid_name, content FROM kids_schedule WHERE user_id = ? AND day_of_week = ? ORDER BY kid_name",
            (user_id, day_of_week),
        ).fetchall()
    finally:
        conn.close()


def list_users_with_kids_schedule() -> list[sqlite3.Row]:
    """Every user who has saved at least one kids_schedule row - what the
    nightly job iterates instead of every user in the system, most of whom
    have never used this feature."""
    conn = get_connection()
    try:
        return conn.execute(
            """
            SELECT DISTINCT u.id AS user_id, u.whatsapp_number, u.timezone
            FROM users u JOIN kids_schedule k ON k.user_id = u.id
            """
        ).fetchall()
    finally:
        conn.close()


def find_kid_schedule_owner_by_whatsapp_number(whatsapp_number: str) -> tuple[int, str] | None:
    """
    New feature (2026-09-15): lets a kid with their own registered bot
    account (e.g. a message from "נועה") ask about their OWN schedule, which
    is actually saved under a PARENT's account (the parent is the one who
    ran action=set/set_week). Resolves by matching the kid's own WhatsApp
    number against a contact a parent saved (see webhook_handler.
    _handle_kids_schedule's phone-number fallback) - every (owner_user_id,
    contact name) pair whose whatsapp_number matches is tried in order until
    one actually has saved kids_schedule rows, since the same kid is
    typically saved as a contact under BOTH parents' accounts (same name),
    and occasionally under more than one spelling (e.g. a leftover "Noa" next
    to a newer "נועה").

    Deliberately searches across ALL contact owners, not just a specific
    "parent" flag - there is no such flag, and every registered user here is
    the same small family (PRD 14.2's allowlist already keeps anyone outside
    it from reaching the bot at all), so this is not the cross-tenant
    privacy concern it would be in a multi-family deployment.

    Returns (owner_user_id, kid_name) for the first match with real data, or
    None if nothing matches (not a registered kid, or no parent has saved
    anything for them yet).
    """
    conn = get_connection()
    try:
        candidates = conn.execute(
            "SELECT DISTINCT owner_user_id, name FROM contacts WHERE whatsapp_number = ? ORDER BY owner_user_id",
            (whatsapp_number,),
        ).fetchall()
        for row in candidates:
            has_rows = conn.execute(
                "SELECT 1 FROM kids_schedule WHERE user_id = ? AND kid_name = ? LIMIT 1",
                (row["owner_user_id"], row["name"]),
            ).fetchone()
            if has_rows:
                return row["owner_user_id"], row["name"]
        return None
    finally:
        conn.close()


def set_daily_meetings_summary_enabled(user_id: int, enabled: bool) -> None:
    """Turns the opt-in daily calendar summary on/off for one user (see
    scheduler.check_and_send_daily_meetings_summaries)."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE users SET daily_meetings_summary_enabled = ? WHERE id = ?",
            (int(enabled), user_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_daily_meetings_summary_enabled(user_id: int) -> bool:
    """Current opt-in state for one user - backs the 'status' action."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT daily_meetings_summary_enabled FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return bool(row["daily_meetings_summary_enabled"]) if row else False
    finally:
        conn.close()


def set_daily_meetings_summary_time(user_id: int, time_str: str) -> None:
    """Sets the per-user send time ('HH:MM', 24h, in the user's own
    timezone) - see src/tools/batch13.py for the 'HH:MM' format validation,
    done before this is ever called."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE users SET daily_meetings_summary_time = ? WHERE id = ?",
            (time_str, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_daily_meetings_summary_time(user_id: int) -> str:
    """Current configured send time for one user - backs the 'status' action."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT daily_meetings_summary_time FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return (row["daily_meetings_summary_time"] if row else None) or "07:00"
    finally:
        conn.close()


def mark_daily_meetings_summary_sent(user_id: int, date_str: str) -> None:
    """Records that today's summary (or today's 'you need to reconnect'
    alert) was already sent - date_str is the user's own local date
    ('YYYY-MM-DD'), so the frequent interval check doesn't re-send once the
    target time has passed for the day (see
    scheduler.check_and_send_daily_meetings_summaries)."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE users SET daily_meetings_summary_last_sent_date = ? WHERE id = ?",
            (date_str, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def clear_daily_meetings_summary_sent_marker(user_id: int) -> None:
    """
    2026-09-18 bug fix: resets the 'already sent today' marker. Needed when
    the user changes their send time AFTER today's summary (under the OLD
    time) already went out - without this, check_and_send_daily_meetings_
    summaries would still see today's date in daily_meetings_summary_last_
    sent_date and skip today's send under the new time, contradicting the
    "יישלח היום" confirmation _handle_daily_meetings_summary just gave them.
    """
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE users SET daily_meetings_summary_last_sent_date = NULL WHERE id = ?",
            (user_id,),
        )
        conn.commit()
    finally:
        conn.close()


def list_users_with_daily_meetings_summary_enabled() -> list[sqlite3.Row]:
    """Every user who has opted in - what the morning job iterates instead
    of every user in the system. Includes their own configured time and
    last-sent marker so the caller can decide, per user, whether today's
    send is due yet."""
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT id AS user_id, whatsapp_number, timezone, display_name, "
            "daily_meetings_summary_time, daily_meetings_summary_last_sent_date "
            "FROM users WHERE daily_meetings_summary_enabled = 1 AND is_active = 1"
        ).fetchall()
    finally:
        conn.close()


def list_reminder_delivery_failure_notification_numbers() -> list[str]:
    """
    WhatsApp numbers of every user who should hear about a reminder to a
    kid that genuinely, finally failed to deliver (2026-09-19) - both
    parents, regardless of which of them created that specific reminder.
    See check_and_send_reminders (the synchronous failure path) and
    whatsapp.handle_delivery_status (the async 24h-window-retry-also-
    failed path), which both notify everyone this returns.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT whatsapp_number FROM users WHERE notify_on_reminder_delivery_failure = 1 AND is_active = 1"
        ).fetchall()
        return [row["whatsapp_number"] for row in rows]
    finally:
        conn.close()


MAX_ACTIVE_PERSISTENT_REMINDERS_PER_USER = 20

# Named so webhook_handler's create-confirmation reply can state the real
# values instead of hardcoding them a second time (2026-09-18: that
# duplication was flagged as a bug risk - text would silently go stale if
# these defaults ever changed without updating the reply text too).
DEFAULT_PERSISTENT_REMINDER_RETRY_INTERVAL_MINUTES = 5
DEFAULT_PERSISTENT_REMINDER_MAX_ATTEMPTS = 3


def save_persistent_reminder(
    owner_user_id: int, recipient_contact_id: int, content: str, next_trigger_at: datetime,
    retry_interval_minutes: int = DEFAULT_PERSISTENT_REMINDER_RETRY_INTERVAL_MINUTES,
    max_attempts: int = DEFAULT_PERSISTENT_REMINDER_MAX_ATTEMPTS,
    schedule_type: str = "once", schedule_time: str | None = None, schedule_days: str | None = None,
) -> int | None:
    """
    Creates a new nagging reminder. Returns its id, or None if the owner
    already has MAX_ACTIVE_PERSISTENT_REMINDERS_PER_USER pending ones - a
    cap in the same spirit as MAX_OPEN_TASKS_PER_USER, since each one polls
    every retry_interval_minutes until resolved and an unbounded pile of
    them would mean an unbounded stream of nags.

    schedule_type/schedule_time/schedule_days (2026-09-17): same convention
    as reminders.schedule_type - 'once' (the original behavior, terminally
    resolves once confirmed/escalated), or 'daily'/'weekly' (resets itself
    for the next occurrence instead - see
    reschedule_persistent_reminder_for_next_occurrence). Stored here purely
    for the scheduler/confirmation-handler to read back when deciding how
    to resolve a cycle; this function itself does nothing with them beyond
    storing.
    """
    conn = get_connection()
    try:
        open_count = conn.execute(
            "SELECT COUNT(*) FROM persistent_reminders WHERE owner_user_id = ? AND status = 'pending'",
            (owner_user_id,),
        ).fetchone()[0]
        if open_count >= MAX_ACTIVE_PERSISTENT_REMINDERS_PER_USER:
            return None
        cur = conn.execute(
            """
            INSERT INTO persistent_reminders
                (owner_user_id, recipient_contact_id, content, retry_interval_minutes, max_attempts,
                 next_trigger_at, schedule_type, schedule_time, schedule_days)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (owner_user_id, recipient_contact_id, content, retry_interval_minutes, max_attempts,
             next_trigger_at.isoformat(), schedule_type, schedule_time, schedule_days),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_due_persistent_reminders(now_utc_iso: str) -> list[sqlite3.Row]:
    """
    Every 'pending' persistent reminder that is due (next_trigger_at <= now),
    across all owners - what scheduler.check_and_send_persistent_reminders
    polls. Joined with both the recipient contact's and the owner's own
    name/number, so the caller never needs a second lookup to know who to
    message (whether that's a fresh nag to the recipient, or an escalation
    to the owner once attempts_sent has reached max_attempts).
    """
    conn = get_connection()
    try:
        return conn.execute(
            """
            SELECT pr.id, pr.owner_user_id, pr.content, pr.retry_interval_minutes,
                   pr.max_attempts, pr.attempts_sent,
                   pr.schedule_type, pr.schedule_time, pr.schedule_days,
                   c.name AS recipient_name, c.whatsapp_number AS recipient_whatsapp_number,
                   u.whatsapp_number AS owner_whatsapp_number, u.display_name AS owner_display_name,
                   u.kid_facing_role AS owner_kid_facing_role,
                   u.timezone AS owner_timezone
            FROM persistent_reminders pr
            JOIN contacts c ON c.id = pr.recipient_contact_id
            JOIN users u ON u.id = pr.owner_user_id
            WHERE pr.status = 'pending' AND pr.next_trigger_at <= ?
            ORDER BY pr.next_trigger_at
            """,
            (now_utc_iso,),
        ).fetchall()
    finally:
        conn.close()


def advance_persistent_reminder(reminder_id: int, next_trigger_at: datetime) -> None:
    """Records that one more nag was just sent - bumps attempts_sent and
    schedules the next one."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE persistent_reminders SET attempts_sent = attempts_sent + 1, next_trigger_at = ? WHERE id = ?",
            (next_trigger_at.isoformat(), reminder_id),
        )
        conn.commit()
    finally:
        conn.close()


def mark_persistent_reminder_escalated(reminder_id: int) -> None:
    """max_attempts was reached with no confirmation - stops the nagging
    and records that the owner was told."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE persistent_reminders SET status = 'escalated', resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
            (reminder_id,),
        )
        conn.commit()
    finally:
        conn.close()


def reschedule_persistent_reminder_for_next_occurrence(reminder_id: int, next_trigger_at: datetime) -> bool:
    """
    2026-09-17: for a recurring (daily/weekly) persistent reminder, resets
    it for its NEXT occurrence instead of terminally resolving - called by
    both webhook_handler._check_task_confirmation (after a real
    confirmation) and scheduler.check_and_send_persistent_reminders (after
    an escalation), whenever the row's own schedule_type is not 'once'.
    Deliberately does NOT touch resolved_at or status - the row stays
    'pending' throughout its recurring life, only ever leaving that state
    via cancel_persistent_reminder.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE persistent_reminders SET attempts_sent = 0, next_trigger_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (next_trigger_at.isoformat(), reminder_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def mark_persistent_reminder_done(reminder_id: int) -> bool:
    """
    The recipient confirmed. No owner_user_id ownership check here (unlike
    most other mutations in this file) - the recipient confirming is by
    definition someone OTHER than the owner, resolved by matching their own
    WhatsApp number to the contact on the row (see
    find_pending_persistent_reminder_for_number), not by being its owner.
    Only affects a still-'pending' row, so a row already resolved (done/
    escalated/cancelled) can't be double-confirmed.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE persistent_reminders SET status = 'done', resolved_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND status = 'pending'",
            (reminder_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_active_persistent_reminders(owner_user_id: int, recipient_name: str | None = None) -> list[sqlite3.Row]:
    """
    This owner's still-pending nagging reminders - backs the 'list' action.
    recipient_name (2026-09-17): scopes the list to one kid, so "מה יש
    לדני ברשימת התזכורות" shows only their own, not everyone's - omit for
    the original "everyone" behavior.
    """
    conn = get_connection()
    try:
        sql = (
            "SELECT pr.id, pr.content, pr.attempts_sent, pr.max_attempts, pr.retry_interval_minutes, "
            "pr.schedule_type, pr.schedule_time, pr.schedule_days, c.name AS recipient_name "
            "FROM persistent_reminders pr "
            "JOIN contacts c ON c.id = pr.recipient_contact_id "
            "WHERE pr.owner_user_id = ? AND pr.status = 'pending'"
        )
        params: list = [owner_user_id]
        if recipient_name:
            sql += " AND c.name = ?"
            params.append(recipient_name)
        sql += " ORDER BY c.name, pr.created_at"
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def find_persistent_reminder_by_match(owner_user_id: int, match: str) -> sqlite3.Row | None:
    """
    Finds exactly one of this owner's pending nagging reminders by content
    or recipient name - same disambiguation style as reminder_manage's
    match_content. Checks containment BOTH ways (2026-09-18, found live):
    match found within content/name (e.g. match="כלב" hits content="להאכיל
    את הכלב"), OR content/name found within match (e.g. Gemini naturally
    phrases match as "KidName content-phrase" combined, which is not a
    substring of either field alone but DOES fully contain each of them) -
    a real request classified live for a "cancel X's reminder about Y" ended
    up with exactly that combined phrasing, and the one-directional check
    alone missed it. Returns None if zero or more than one match (the
    caller should ask the user to be more specific).
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT pr.id, pr.content, c.name AS recipient_name
            FROM persistent_reminders pr
            JOIN contacts c ON c.id = pr.recipient_contact_id
            WHERE pr.owner_user_id = ? AND pr.status = 'pending'
              AND (
                pr.content LIKE ? OR c.name LIKE ?
                OR ? LIKE '%' || pr.content || '%'
                OR ? LIKE '%' || c.name || '%'
              )
            """,
            (owner_user_id, f"%{match}%", f"%{match}%", match, match),
        ).fetchall()
        return rows[0] if len(rows) == 1 else None
    finally:
        conn.close()


def cancel_persistent_reminder(reminder_id: int, owner_user_id: int) -> bool:
    """Stops the nagging early. Ownership-scoped (unlike mark_..._done,
    which the recipient triggers) - only the owner who created it may
    cancel it."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE persistent_reminders SET status = 'cancelled', resolved_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND owner_user_id = ? AND status = 'pending'",
            (reminder_id, owner_user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def find_pending_persistent_reminder_for_number(whatsapp_number: str, now_utc_iso: str) -> sqlite3.Row | None:
    """
    The most recently created nagging reminder addressed to this WhatsApp
    number that is CURRENTLY due/mid-cycle (next_trigger_at <= now), if
    any - what webhook_handler._check_task_confirmation checks before
    treating an incoming message as a possible "I did it" confirmation.
    Resolves by matching the recipient's own number against the contact
    row, the same phone-number-based technique
    find_kid_schedule_owner_by_whatsapp_number already uses - a kid
    confirming is a registered user in their own right, not the owner of
    the reminder they're confirming.

    2026-09-18 bug fix: a recurring (daily/weekly) reminder stays
    status='pending' FOREVER between occurrences (see
    reschedule_persistent_reminder_for_next_occurrence) - it's still
    "active", just not due yet. Originally this only filtered on
    status='pending', which meant a recurring reminder rescheduled for
    tomorrow evening would still intercept and Gemini-classify every
    message the kid sent tomorrow MORNING as a possible confirmation of
    something they weren't even being nagged about yet. Filtering on
    next_trigger_at <= now restricts this to reminders that have actually
    started nagging this cycle (or are a one-off already due), matching
    the same "is this actually due" gate get_due_persistent_reminders uses.

    If more than one is pending for the same number at once (e.g. two
    different parents each nagged the same kid about something different),
    only the most recent one is checked - a deliberate simplification for this
    bot's small-family scale, not a real ambiguity-resolution mechanism.
    """
    conn = get_connection()
    try:
        return conn.execute(
            """
            SELECT pr.id, pr.content, pr.owner_user_id,
                   pr.schedule_type, pr.schedule_time, pr.schedule_days,
                   c.name AS recipient_name,
                   u.whatsapp_number AS owner_whatsapp_number, u.display_name AS owner_display_name,
                   u.timezone AS owner_timezone
            FROM persistent_reminders pr
            JOIN contacts c ON c.id = pr.recipient_contact_id
            JOIN users u ON u.id = pr.owner_user_id
            WHERE c.whatsapp_number = ? AND pr.status = 'pending' AND pr.next_trigger_at <= ?
            ORDER BY pr.created_at DESC, pr.id DESC
            LIMIT 1
            """,
            (whatsapp_number, now_utc_iso),
        ).fetchone()
    finally:
        conn.close()


def log_api_usage(provider: str, input_tokens: int | None = None, output_tokens: int | None = None) -> None:
    """
    Records one external API call for the admin-only usage/cost report (see
    webhook_handler._handle_usage_status). provider: 'gemini_generate' |
    'gemini_embed' | 'ship24'. Failures here must never break the calling
    feature - callers should wrap this in try/except, same as the existing
    "non-fatal, log and continue" convention for background/secondary work
    (see _embed_message_in_background).
    """
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO api_usage_log (provider, input_tokens, output_tokens) VALUES (?, ?, ?)",
            (provider, input_tokens, output_tokens),
        )
        conn.commit()
    finally:
        conn.close()


def log_shadow_classification(
    whatsapp_message_id: str | None,
    raw_content: str | None,
    old_intent: str,
    new_tool: str | None,
    new_args: str | None,
    error: str | None,
) -> None:
    """
    Records one shadow-mode comparison (see src.tools.shadow). Failures here
    must never break the calling feature - same "non-fatal, log and
    continue" convention as log_api_usage; the caller already wraps this in
    its own try/except for exactly that reason.
    """
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO intent_shadow_log
                (whatsapp_message_id, raw_content, old_intent, new_tool, new_args, error)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (whatsapp_message_id, raw_content, old_intent, new_tool, new_args, error),
        )
        conn.commit()
    finally:
        conn.close()


def get_usage_summary() -> dict:
    """
    Aggregates api_usage_log into today/this-month totals per provider, for
    the admin usage/cost report. Returns:
    {
      "gemini_generate": {"today": {"calls", "input_tokens", "output_tokens"}, "month": {...}},
      "gemini_embed": {"today": {...}, "month": {...}},
      "ship24": {"today": {"calls"}, "month": {"calls"}},
    }
    """
    conn = get_connection()
    try:
        result: dict[str, dict] = {}
        for provider in ("gemini_generate", "gemini_embed", "ship24"):
            result[provider] = {}
            for period, since_expr in (("today", "start of day"), ("month", "start of month")):
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS calls,
                           COALESCE(SUM(input_tokens), 0) AS input_tokens,
                           COALESCE(SUM(output_tokens), 0) AS output_tokens
                    FROM api_usage_log
                    WHERE provider = ? AND created_at >= datetime('now', ?)
                    """,
                    (provider, since_expr),
                ).fetchone()
                result[provider][period] = dict(row)
        return result
    finally:
        conn.close()


def get_cost_report_state() -> sqlite3.Row | None:
    """The single cost_report_state row - see its own docstring in _run_migrations."""
    conn = get_connection()
    try:
        return conn.execute("SELECT * FROM cost_report_state WHERE id = 1").fetchone()
    finally:
        conn.close()


def mark_cost_report_sent(date_str: str) -> None:
    """date_str: the owner's own local date (YYYY-MM-DD) the periodic report was just sent on."""
    conn = get_connection()
    try:
        conn.execute("UPDATE cost_report_state SET last_report_sent_date = ? WHERE id = 1", (date_str,))
        conn.commit()
    finally:
        conn.close()


def mark_budget_alert_sent() -> None:
    """Records that the budget-crossed alert just fired, so it doesn't repeat every check."""
    conn = get_connection()
    try:
        conn.execute("UPDATE cost_report_state SET last_budget_alert_sent_at = CURRENT_TIMESTAMP WHERE id = 1")
        conn.commit()
    finally:
        conn.close()


# ===== Context-Aware Gatekeeper (2026-09-27) - delivery policy =====


def get_proactive_settings(user_id: int) -> sqlite3.Row | None:
    """The user's proactive-delivery settings row, or None if they've never
    touched manage_proactive_settings (equivalent to enabled=False - see
    src.proactive.should_deliver_now, which treats a missing row and
    enabled=0 identically)."""
    conn = get_connection()
    try:
        return conn.execute("SELECT * FROM proactive_settings WHERE user_id = ?", (user_id,)).fetchone()
    finally:
        conn.close()


def set_proactive_enabled(user_id: int, enabled: bool) -> None:
    """Upsert - the settings row is created here on first use (action=enable),
    with every other column left at its schema default."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO proactive_settings (user_id, enabled) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET enabled = excluded.enabled",
            (user_id, enabled),
        )
        conn.commit()
    finally:
        conn.close()


def set_proactive_quiet_hours(user_id: int, start: str, end: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO proactive_settings (user_id, quiet_hours_start, quiet_hours_end) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET quiet_hours_start = excluded.quiet_hours_start, "
            "quiet_hours_end = excluded.quiet_hours_end",
            (user_id, start, end),
        )
        conn.commit()
    finally:
        conn.close()


def set_proactive_daily_cap(user_id: int, cap: int) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO proactive_settings (user_id, daily_cap) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET daily_cap = excluded.daily_cap",
            (user_id, cap),
        )
        conn.commit()
    finally:
        conn.close()


def set_proactive_meeting_lead_time(user_id: int, minutes: int) -> None:
    """How long before a meeting starts to send its pre-brief - see
    scheduler.check_and_send_meeting_prebriefs."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO proactive_settings (user_id, meeting_lead_time_minutes) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET meeting_lead_time_minutes = excluded.meeting_lead_time_minutes",
            (user_id, minutes),
        )
        conn.commit()
    finally:
        conn.close()


def set_proactive_status_quiet_until(user_id: int, until_utc_iso: str) -> None:
    """until_utc_iso: an ISO timestamp in UTC - the "אני בפגישה עד 16:00" /
    "שקט עד מחר" override (see webhook_handler._handle_manage_proactive_
    settings for how a natural-language duration becomes this timestamp)."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO proactive_settings (user_id, status_quiet_until) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET status_quiet_until = excluded.status_quiet_until",
            (user_id, until_utc_iso),
        )
        conn.commit()
    finally:
        conn.close()


def clear_proactive_status_quiet_until(user_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute("UPDATE proactive_settings SET status_quiet_until = NULL WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def add_vip_sender(owner_user_id: int, identifier: str, label: str | None = None) -> None:
    """Upsert by (owner_user_id, identifier) - re-adding an existing VIP just updates its label."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO vip_senders (owner_user_id, identifier, label) VALUES (?, ?, ?) "
            "ON CONFLICT(owner_user_id, identifier) DO UPDATE SET label = excluded.label",
            (owner_user_id, identifier.strip().lower(), label),
        )
        conn.commit()
    finally:
        conn.close()


def list_vip_senders(owner_user_id: int) -> list[sqlite3.Row]:
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT * FROM vip_senders WHERE owner_user_id = ? ORDER BY id", (owner_user_id,)
        ).fetchall()
    finally:
        conn.close()


def remove_vip_sender(owner_user_id: int, identifier: str) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM vip_senders WHERE owner_user_id = ? AND identifier = ?",
            (owner_user_id, identifier.strip().lower()),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def is_vip_sender(owner_user_id: int, identifier: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM vip_senders WHERE owner_user_id = ? AND identifier = ?",
            (owner_user_id, identifier.strip().lower()),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def count_todays_proactive_notifications(user_id: int) -> int:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM proactive_notification_log "
            "WHERE user_id = ? AND created_at >= datetime('now', 'start of day')",
            (user_id,),
        ).fetchone()
        return row["c"]
    finally:
        conn.close()


def log_proactive_notification(user_id: int, category: str, summary: str | None = None) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO proactive_notification_log (user_id, category, summary) VALUES (?, ?, ?)",
            (user_id, category, summary),
        )
        conn.commit()
    finally:
        conn.close()


def reserve_proactive_notification_slot(user_id: int, cap: int, category: str, summary: str | None = None) -> int | None:
    """
    Race fix (2026-09-27): the old pattern - should_deliver_now reads
    count_todays_proactive_notifications, then a SEPARATE call to
    log_proactive_notification writes the row - has a real TOCTOU gap.
    check_and_monitor_calendar_changes and check_and_monitor_new_emails run
    on independent APScheduler intervals and can genuinely overlap for the
    same user; if both read the count before either writes, the daily cap
    could be exceeded by one message. This does the check-and-insert as a
    single atomic SQL statement instead - SQLite executes the INSERT...
    SELECT...WHERE as one operation, so there is no window between "is
    there room" and "reserve the slot" for another thread to land in.

    Returns the new row's id if reserved, or None if the cap was already
    reached by the time this ran (race or not) - the caller (see
    proactive.deliver_proactive_message) releases the row with
    delete_proactive_notification_log_row if the WhatsApp send that follows
    ends up failing, so a failed attempt doesn't permanently waste a slot
    of the daily quota.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO proactive_notification_log (user_id, category, summary)
            SELECT ?, ?, ?
            WHERE (
                SELECT COUNT(*) FROM proactive_notification_log
                WHERE user_id = ? AND created_at >= datetime('now', 'start of day')
            ) < ?
            """,
            (user_id, category, summary, user_id, cap),
        )
        conn.commit()
        return cur.lastrowid if cur.rowcount > 0 else None
    finally:
        conn.close()


def delete_proactive_notification_log_row(row_id: int) -> None:
    """Releases a slot reserve_proactive_notification_slot reserved, when
    the WhatsApp send that was supposed to follow it failed - see
    proactive.deliver_proactive_message."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM proactive_notification_log WHERE id = ?", (row_id,))
        conn.commit()
    finally:
        conn.close()


def defer_proactive_message(user_id: int, category: str, body: str, identifier: str | None = None) -> None:
    """Holds a notification that was blocked by quiet hours or an explicit
    "busy" status, for delivery once the window opens - see
    scheduler.check_and_deliver_deferred_notifications."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO deferred_proactive_notifications (user_id, category, body, identifier) VALUES (?, ?, ?, ?)",
            (user_id, category, body, identifier),
        )
        conn.commit()
    finally:
        conn.close()


def get_deferred_notifications(user_id: int) -> list[sqlite3.Row]:
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT * FROM deferred_proactive_notifications WHERE user_id = ? ORDER BY created_at", (user_id,)
        ).fetchall()
    finally:
        conn.close()


def delete_deferred_notifications(ids: list[int]) -> None:
    if not ids:
        return
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM deferred_proactive_notifications WHERE id IN ({placeholders})", ids)
        conn.commit()
    finally:
        conn.close()


def get_calendar_snapshot(user_id: int) -> dict:
    """Returns {event_id: {"summary", "start", "end"}} for every event
    currently snapshotted for this user - the collector's own "what did I
    see last time" baseline to diff the live Calendar API result against."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT event_id, summary, start, end FROM calendar_snapshot WHERE user_id = ?", (user_id,)
        ).fetchall()
        return {r["event_id"]: {"summary": r["summary"], "start": r["start"], "end": r["end"]} for r in rows}
    finally:
        conn.close()


def upsert_calendar_snapshot(user_id: int, event_id: str, summary: str, start: str, end: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO calendar_snapshot (user_id, event_id, summary, start, end, last_seen_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, event_id) DO UPDATE SET
                summary = excluded.summary, start = excluded.start, end = excluded.end,
                last_seen_at = excluded.last_seen_at
            """,
            (user_id, event_id, summary, start, end),
        )
        conn.commit()
    finally:
        conn.close()


def delete_calendar_snapshot_event(user_id: int, event_id: str) -> None:
    """Called when the collector confirms an event genuinely disappeared
    (cancelled), so it isn't reported as "cancelled" again on the next poll."""
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM calendar_snapshot WHERE user_id = ? AND event_id = ?", (user_id, event_id)
        )
        conn.commit()
    finally:
        conn.close()


def is_meeting_prebriefed(user_id: int, event_id: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM meeting_prebrief_sent WHERE user_id = ? AND event_id = ?", (user_id, event_id)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def mark_meeting_prebriefed(user_id: int, event_id: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO meeting_prebrief_sent (user_id, event_id) VALUES (?, ?)",
            (user_id, event_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_old_meeting_prebrief_sent(days: int) -> int:
    """Maintenance: meeting_prebrief_sent rows are only ever relevant for a
    few minutes (the meeting's own lead-time window) - pruned by the same
    daily cleanup job as gmail_seen_messages/proactive_notification_log."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM meeting_prebrief_sent WHERE sent_at < datetime('now', ?)", (f"-{days} days",)
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def has_any_gmail_seen_message(user_id: int) -> bool:
    """True once the email-monitor collector has polled this user at least
    once before - the first-run guard (see scheduler.check_and_monitor_
    new_emails) that stops enabling proactive mode from immediately
    reporting every email from the last 24h as if it just arrived."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT 1 FROM gmail_seen_messages WHERE user_id = ? LIMIT 1", (user_id,)).fetchone()
        return row is not None
    finally:
        conn.close()


def is_gmail_message_seen(user_id: int, message_id: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM gmail_seen_messages WHERE user_id = ? AND message_id = ?",
            (user_id, message_id),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def mark_gmail_message_seen(user_id: int, message_id: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO gmail_seen_messages (user_id, message_id) VALUES (?, ?)",
            (user_id, message_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_old_gmail_seen_messages(days: int) -> int:
    """
    Maintenance fix (2026-09-27): gmail_seen_messages has no cleanup - one
    row per email ever seen, for every proactive-enabled user, forever.
    Not a correctness issue at personal-bot scale, but unbounded. Called
    daily by scheduler.check_and_cleanup_proactive_data. A message this old
    is long past being "genuinely new" on any future poll anyway, so
    pruning it is safe - it only ever existed to answer "have I seen this
    id before". Returns how many rows were removed.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM gmail_seen_messages WHERE created_at < datetime('now', ?)", (f"-{days} days",)
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def delete_old_proactive_notification_log(days: int) -> int:
    """
    Maintenance fix (2026-09-27): proactive_notification_log has no
    cleanup either - only today's rows are ever read (see
    count_todays_proactive_notifications/reserve_proactive_notification_
    slot, both scoped to "start of day"), so anything older than a day is
    already dead weight for the app's own logic; kept around this long
    only in case the admin ever wants to look back at what was sent. Called
    daily by scheduler.check_and_cleanup_proactive_data. Returns how many
    rows were removed.
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM proactive_notification_log WHERE created_at < datetime('now', ?)", (f"-{days} days",)
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()

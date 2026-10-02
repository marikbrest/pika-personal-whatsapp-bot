# PRD — WhatsApp Personal Assistant (working name: "Assistant")

**Status:** Draft v1
**Purpose of this document:** to serve as complete context for the actual build
(including for an automated coding agent)

---

## 1. Vision and background

A personal assistant that runs through WhatsApp (text + voice messages), built on Gemini
for natural language understanding, providing:

- Reminders, calendar (Google Calendar), and task management
- Reading email (Gmail) and replying to it (through a draft/approval flow)
- Learning user behaviour and preferences over time
- Persisted conversation history
- Saved links (with a permanent copy of the content, not just the URL)

**Founding principles:**

1. Store permanent copies of content, not just links — protects against content that is
   deleted or moves behind a paywall
2. Local-first: SQLite + Cloudflare Tunnel, no cloud dependency from day one
3. Multi-tenant-ready from day one (a `user_id` column on every table) — even while
   there is only a single user
4. Irreversible actions (sending email) require explicit approval — never automatic
5. All code in GitHub; secrets never committed

---

## 2. Scope — what belongs to each phase

### Phase 1 — MVP (the core)

- [x] WhatsApp webhook (text)
- [x] WhatsApp webhook (voice messages — audio sent directly to Gemini)
- [x] Intent parsing via Gemini (structured JSON)
- [x] SQLite: full conversation history
- [x] A basic reminder working end to end (creation → storage → scheduler → outbound send)
- [ ] Basic user profile (habits, hours, preferred tone) — fed as context into every
      Gemini call
- [x] Running on Windows (home machine) + Cloudflare Tunnel

### Phase 2 — Google Calendar + Gmail reading

- [x] Google OAuth flow (Calendar scope)
- [x] Creating/updating calendar events through conversation
- [x] Gmail OAuth (scope: `gmail.readonly`)
- [x] Reading and summarising email on request

### Phase 3 — Gmail replies + saved links

- [x] Extended Gmail scope (`gmail.send`, `gmail.drafts.create`, under control)
- [x] The "draft → approve in WhatsApp → send" model
- [ ] Saved links: fetch + extraction + permanent copy storage
- [ ] Semantic search (embeddings) over conversations and saved links

### Explicitly out of scope for now (future, not being built)

- Billing / subscription tiers
- ~~Admin panel for managing multiple users~~ — built ahead of schedule; see the README
- A "motivation" layer (streaks/personas) — an idea borrowed from Botivation, for a
  separate future phase
- A dedicated mobile app — everything goes through WhatsApp

---

## 3. Architecture

```
WhatsApp (user)
   │  text / voice message
   ▼
Meta WhatsApp Cloud API ── webhook ──▶ Cloudflare Tunnel ──▶ local server (Windows)
                                                                    │
                                                    ┌───────────────┼────────────────┐
                                                    ▼               ▼                ▼
                                            Webhook Handler   Media Downloader   Auth/Token Store
                                                    │           (audio files)     (Google OAuth,
                                                    ▼                              WhatsApp token)
                                            Intent Parser (Gemini API)
                                            - text: direct prompt
                                            - audio: sent straight to Gemini (native audio support)
                                                    │
                                                    ▼
                                            Router, by intent type
                    ┌───────────────┬───────────────┼───────────────┬────────────────┐
                    ▼               ▼               ▼               ▼                ▼
                reminder         calendar         email (Gmail)   list/task      saved link
                    │               │               │               │                │
                    └───────────────┴───────┬───────┴───────────────┴────────────────┘
                                             ▼
                                        SQLite (local)
                                             │
                                             ▼
                                   Scheduler (checks every minute)
                                             │
                                             ▼
                              outbound message back to WhatsApp Cloud API
```

---

## 4. Database schema (SQLite)

> **Guiding principle:** every table carries `user_id` from day one, even with a single
> user.

```sql
-- Users
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    whatsapp_number TEXT UNIQUE NOT NULL,
    display_name TEXT,
    timezone TEXT DEFAULT 'Asia/Jerusalem',
    preferred_tone TEXT DEFAULT 'neutral',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- External tokens (encrypted at the application layer before writing)
CREATE TABLE oauth_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    provider TEXT NOT NULL,             -- 'google_calendar' | 'google_gmail'
    access_token_encrypted TEXT NOT NULL,
    refresh_token_encrypted TEXT NOT NULL,
    scope TEXT NOT NULL,
    expires_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Message history (every conversation, inbound and outbound)
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    direction TEXT NOT NULL,            -- 'inbound' | 'outbound'
    message_type TEXT NOT NULL,         -- 'text' | 'audio'
    raw_content TEXT,                   -- original text / transcription
    parsed_intent TEXT,                 -- JSON from Gemini
    whatsapp_message_id TEXT UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Reminders
CREATE TABLE reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    content TEXT NOT NULL,
    schedule_type TEXT NOT NULL,        -- 'once' | 'daily' | 'weekly' | 'monthly'
    schedule_time TEXT NOT NULL,        -- '09:00' etc.
    schedule_days TEXT,                 -- for weekly frequency: 'sun,tue'
    next_trigger_at TIMESTAMP NOT NULL,
    is_active BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Tasks / lists
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    list_name TEXT DEFAULT 'default',   -- 'groceries', 'work', etc.
    content TEXT NOT NULL,
    is_done BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

-- Saved links (Phase 3)
CREATE TABLE saved_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    original_url TEXT NOT NULL,
    title TEXT,
    content_snapshot TEXT,              -- the permanent copy of the content
    content_type TEXT,                  -- 'article' | 'video' | 'other'
    fetch_status TEXT DEFAULT 'pending', -- 'pending' | 'success' | 'paywalled' | 'failed'
    embedding BLOB,                     -- for semantic search (Phase 3)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Email drafts awaiting approval (Phase 3)
CREATE TABLE email_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    to_address TEXT NOT NULL,
    subject TEXT,
    body TEXT NOT NULL,
    status TEXT DEFAULT 'pending_approval', -- 'pending_approval' | 'approved' | 'rejected' | 'sent'
    original_thread_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMP
);

-- Behavioural profile (accumulated over time, fed as context to Gemini)
CREATE TABLE user_profile_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    fact_key TEXT NOT NULL,             -- 'wake_time' | 'ignores_reminder_type' etc.
    fact_value TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 5. Message flow — end to end

**Example: "remind me every day at 9 to take my vitamins"**

1. WhatsApp Cloud API sends a webhook POST to the server (through Cloudflare Tunnel)
2. `webhook_handler` resolves `user_id` from the phone number and stores the raw message
   in `messages`
3. If `message_type == audio`: downloads the media file from the WhatsApp Media API and
   sends it straight to Gemini
4. `intent_parser` sends Gemini a prompt containing:
   - the message (text/audio)
   - context: the user profile (`user_profile_facts`)
   - a system prompt forcing JSON output:
     `{"intent": "create_reminder", "content": "take vitamins", "schedule": {"type": "daily", "time": "09:00"}}`
5. `router` receives the JSON and creates a row in `reminders`
6. Sends a confirmation message back to the user — a service message inside the 24-hour
   window, at no additional cost (as of writing; re-check Meta pricing changes effective
   2026-10-01)
7. `scheduler` (running every minute) looks for `reminders` where
   `next_trigger_at <= now`, sends an outbound message, and updates `next_trigger_at`
   for the following occurrence

---

## 6. External integrations

| Service | Used for | Scope / permissions |
|---|---|---|
| WhatsApp Cloud API | receiving and sending messages | official Business Account, webhook verification token |
| Gemini API | intent understanding, audio transcription, drafting replies | Flash model by default (cost/performance) |
| Google Calendar API | Phase 2 | `calendar.events` |
| Gmail API | Phase 2 (read) / Phase 3 (send) | `gmail.readonly` → extended to `gmail.send`, `gmail.drafts.create` |
| Cloudflare Tunnel | exposing the local server to the internet | free |

**Note on message templates:** outbound messages (reminders) sent **outside** the
24-hour window require a template pre-approved in Meta Business Manager. A reminder
template (utility category) must be submitted and approved **before** relying on the
scheduler for outbound delivery to passive recipients — approval can take several days.

---

## 7. Setup — Windows machine (Phase 1)

1. **Cloudflare Tunnel**
   - Install `cloudflared` (Windows binary)
   - `cloudflared tunnel login` → create a tunnel → map a subdomain to the local port
     (e.g. `localhost:8000`)
   - Run as a Windows service so it comes back automatically after a restart

2. **Runtime environment**
   - Python 3.11+
   - A dedicated `venv`, with `requirements.txt` defined in the repo

3. **Windows settings for reliability**
   - Power Options → "Never sleep" while plugged in
   - Windows Update → schedule restarts for a non-critical hour, or set Active Hours
   - The service runs under Task Scheduler with restart-on-failure behaviour

4. **Environment variables** (`.env`, never committed)

   ```
   WHATSAPP_ACCESS_TOKEN=
   WHATSAPP_PHONE_NUMBER_ID=
   WHATSAPP_WEBHOOK_VERIFY_TOKEN=
   WHATSAPP_APP_SECRET=
   GEMINI_API_KEY=
   GOOGLE_CLIENT_ID=
   GOOGLE_CLIENT_SECRET=
   GOOGLE_REDIRECT_URI=
   TOKEN_ENCRYPTION_KEY=
   DB_PATH=./data/assistant.db
   ADMIN_HOST=
   ADMIN_ALLOWED_EMAIL=
   ```

---

## 8. Proposed repository structure

```
assistant/
├── src/
│   ├── webhook_handler.py
│   ├── intent_parser.py
│   ├── scheduler.py
│   ├── router.py
│   ├── admin_handler.py
│   ├── oauth_handler.py
│   ├── timing_stats.py
│   ├── db/
│   │   ├── schema.sql
│   │   └── models.py
│   ├── integrations/
│   │   ├── whatsapp.py
│   │   ├── gemini.py
│   │   ├── google_oauth.py
│   │   ├── google_calendar.py
│   │   ├── gmail.py
│   │   ├── weather.py
│   │   └── markets.py
│   └── config.py
├── scripts/                   # startup, status, and backup scripts
├── data/                      # .gitignore'd — holds the actual DB
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
└── PRD.md                     # this document
```

`.gitignore` must include:

```
.env
data/*.db
*.db
tokens/
backups/
__pycache__/
```

---

## 9. Open risks (not yet decided)

| Risk | Description | Status |
|---|---|---|
| Paywalled content | How to handle saving a link that requires registration or payment | Open — to be decided in Phase 3 |
| Media downloads via Cloud API | Images/video sent over WhatsApp — store them? how? | Open |
| Semantic search strategy | Local embeddings (free, slower) versus API-based (Gemini embeddings, costs money) | Open — decide in Phase 3 |
| WhatsApp pricing change from 2026-10-01 | Meta moving to charging for service messages inside the 24-hour window | Monitor; does not block Phase 1 |
| Home machine reliability | Power/internet outages, automatic restarts | Partially managed in section 7, plus catch-up logic in 12.2 — known and accepted for MVP |
| Duplicate webhook from Meta | The same message may be delivered twice | **Resolved — see section 12.1** |
| `next_trigger_at` calculation / missed reminders | It was undefined how downtime should be handled | **Resolved — see section 12.2** |
| Error handling / retries for Gemini, WhatsApp API, SQLite locking | Was not defined at all | **Resolved — see section 12.3** |
| Managing the encryption key for `oauth_tokens` | It was unclear where and how the encryption key is stored | **Resolved — see section 12.4** |

---

## 10. Estimated costs (personal use, Phase 1)

| Component | Estimated monthly cost |
|---|---|
| WhatsApp Cloud API | $1–5 (expected to rise after 2026-10-01) |
| Gemini API (Flash) | $1–3 |
| Cloudflare Tunnel | $0 |
| Google APIs | $0 |
| Home machine (Phase 1) | $0 (already owned) |
| **Phase 1 total** | **~$2–8/month** |

---

## 11. Note to the building agent

When starting work on the repo:

1. Start with Phase 1 only — do not build Phase 2/3 even if it seems "easy to add now"
2. `user_id` on every table from the first line of code, even with a single user
3. No automatic email sending — always via `email_drafts` plus explicit approval
4. Verify `.gitignore` before the first commit (especially `.env` and `data/*.db`)
5. Start with a single endpoint (webhook receive + echo) and confirm the Cloudflare
   Tunnel works end to end **before** adding logic

---

## 12. Error handling, idempotency, and reminder calculation (added after professional review — 2026-08-03)

> Added before any code was written, so that the core sections (webhook, scheduler) are
> built correctly from the start rather than patched afterwards.

### 12.1 Webhook idempotency

Meta may deliver the same webhook more than once (this is documented, standard behaviour
on their side, not a fault). Without protection this creates duplicate messages and
reminders.

- Add a `UNIQUE` constraint on `whatsapp_message_id` in the `messages` table.
- In `webhook_handler`: before INSERT, check whether `whatsapp_message_id` already
  exists → if so, return `200 OK` (mandatory, otherwise Meta keeps retrying) and do not
  process the message again.

### 12.2 Computing `next_trigger_at` and catch-up after downtime

The logic for computing the next occurrence **must** be defined before writing the
scheduler:

- **`once`**: `next_trigger_at` is taken directly from the requested time. After sending
  → `is_active = 0`.
- **`daily`**: after sending, advance to the next occurrence of that local time.
- **`weekly`**: based on `schedule_days` — compute the next matching day in the list.
- **`monthly`**: a policy is needed for 31/30/29 (for example: day 31 in a month without
  a 31st → the last day of that month).

**Catch-up (the machine was off or asleep when the reminder was due):**

- At startup, the scheduler checks all reminders with `next_trigger_at <= now`.
- Default policy: send **one** consolidated message rather than flooding the chat with a
  separate message per missed trigger, then recompute `next_trigger_at` going forward.
- Avoid a "catch-up loop" — if `next_trigger_at` is far behind `now`, jump directly to
  the next future trigger instead of replaying every missed cycle retroactively.

### 12.3 Error handling and retries

| Failure point | Policy |
|---|---|
| Gemini returns malformed or off-schema JSON | Retry once. If it fails again, send the user a "I didn't understand, could you rephrase?" message and record the failure in `messages.parsed_intent` as `null` for debugging. |
| WhatsApp API returns a rate limit or 5xx | Exponential backoff (3 attempts: 1s, 5s, 20s). If it still fails, write to the local log; do not lose the message. |
| SQLite locked (concurrent writes between the webhook handler and the scheduler) | Use `PRAGMA journal_mode=WAL` (better concurrent read/write than the default) and a reasonable `busy_timeout` on the connection instead of failing immediately. |
| Unexpected general failure while handling a message | A top-level try/except around every handler; send the user a generic error message rather than leaving them without a response, and log the full stack trace for debugging. |

### 12.4 Managing the encryption key (`oauth_tokens`)

`access_token_encrypted` / `refresh_token_encrypted` need their own encryption key, and
it cannot simply sit in the same `.env` as everything else without a clear separation of
concerns:

- The encryption key (`TOKEN_ENCRYPTION_KEY`) is stored in `.env` (never committed, like
  the other secrets) but is managed separately conceptually — generate it once
  (`Fernet.generate_key()` when working with Python's `cryptography` library) and
  document that clearly in the README as a setup step, not merely as one more
  environment variable.
- If the home machine is ever wiped (reformat/restore), without this key every token
  stored in the DB is worthless and a full re-auth is required. Back the key up
  **separately** from the DB itself (for example, not in the same backup file).

---

## 13. Updated note to the building agent

In addition to section 11 — **section 12 is an integral part of Phase 1, not a future
improvement.** The idempotency check and `WAL` mode must be in the code from the very
first commit of the webhook handler and the DB connection, not added "when there is
time".

---

## 14. Information security (added in a security review — 2026-08-03)

### 14.1 Webhook signature verification — mandatory, Phase 1

`WHATSAPP_WEBHOOK_VERIFY_TOKEN` protects **only** the initial handshake (Meta's GET
request at registration time). None of the POST requests that follow are authenticated
by it — anyone who discovers the Cloudflare Tunnel address could send a forged POST.

**The solution (officially documented by Meta):** every POST from Meta arrives with an
`X-Hub-Signature-256` header — an HMAC-SHA256 signature of the request body, keyed with
the **App Secret**.

- In `webhook_handler`, before any processing: compute HMAC-SHA256 over the raw body and
  compare it (constant-time, `hmac.compare_digest`) against the header.
- Signature mismatch → `403`, without touching the DB and without calling Gemini.
- Requires a new environment variable: `WHATSAPP_APP_SECRET`.

### 14.2 Sender allowlist — Phase 1

The bot receives messages from any number that writes to it. Required policy:

- A number not present in the `users` table → **silently ignored** (return `200 OK` to
  Meta, write a log line, do not process, do not call Gemini).
- No automatic user creation from an inbound message. Adding a new user is a manual,
  managed action only (also relevant for a future product: controlled onboarding).
- This also protects the wallet — without it, any stranger sending messages burns Gemini
  calls at your expense.

### 14.3 Privacy with respect to the Gemini API

All message content, email (Phase 2), and documents pass through Gemini. A material
consideration:

- **Free tier:** Google may use the content to improve models (as of writing).
- **Paid tier (billing enabled):** content is not used for training.
- **Decision:** for a project that reads personal email, enable billing from day one,
  even if actual consumption stays within the free allowance. Re-verify against Google
  AI's current terms before the Phase 2 go-live.

### 14.4 Automated backups — Phase 1

The home machine is a single point of failure for all the data (conversations,
reminders, and in future the permanent link archive). A failed disk means everything is
gone.

- A nightly backup script (Task Scheduler): a copy of `data/assistant.db` (using
  `sqlite3 .backup`, not a plain file copy — safe even while the DB is in use) plus the
  media folder.
- Destination: some cloud storage (Google Drive / Backblaze B2 / other) — **encrypted
  before upload** (the DB contains the entire personal history).
- `TOKEN_ENCRYPTION_KEY` is backed up **separately** from the DB backup (see 12.4) — a
  backup containing both together defeats the point of the encryption.
- Run a restore test once before go-live: a backup that has never been restored is not a
  backup.

### 14.5 Separating development from production

Both live on the same machine (a conscious Phase 1 decision), so the separation is
logical rather than physical:

- **Production**: a separate folder (e.g. `C:\apps\assistant-prod`), run as a service,
  updated only by `git pull` from `main` plus a deliberate restart.
- **Development**: a separate clone of the repo, separate branch, different
  port, separate DB (`data/dev.db`).
- The Cloudflare Tunnel points **only** at the production port. Development is tested
  locally or with a separate temporary tunnel.
- Merging to `main` is a statement that "this is production-ready". Do not work directly
  on `main`.

### 14.6 Secret hygiene — consolidated summary

| Secret | Where it lives | Notes |
|---|---|---|
| `WHATSAPP_ACCESS_TOKEN` | `.env` | Use a System User token with minimal permissions, not a personal token |
| `WHATSAPP_APP_SECRET` | `.env` | For webhook signature verification (14.1) |
| `TOKEN_ENCRYPTION_KEY` | `.env` + separate backup | See 12.4, 14.4 |
| Google OAuth tokens | DB, encrypted | Never plaintext |
| GitHub PAT | Not stored at all | Created for a specific purpose and revoked immediately after use |

- Logs: never write tokens or secrets to the log. Message content in logs — debug level
  only, local file, never sent to any external service.

---

## 15. Privacy between users (added 2026-08-07)

Once the system serves more than one person, isolation stops being theoretical:

- Every read is scoped by `user_id` / `owner_user_id`. Message history, contacts,
  reminders, email drafts, and OAuth tokens are never visible across users.
- Mutations that accept an ID (cancelling a reminder, sending or editing an email draft)
  **also verify ownership**, rather than relying on the caller having fetched the ID from
  a correctly scoped list. This is defence in depth against IDOR: convention-based safety
  breaks the moment a new caller is added.
- The Gemini prompt contains an explicit privacy rule: if a user asks about other users,
  the bot must not invent an answer or imply it has access, and should explain that each
  conversation is private. This holds even if the user insists or claims ownership of the
  bot.
- User management (listing, adding, disabling) is restricted to users with
  `is_admin = 1`. Granting admin is a deliberate manual database operation.

# Privacy notes for whoever runs this

If you host Pika for family or friends, you are the data controller for their messages, mail
and calendar. This is what the software actually does, so you can tell your users the truth
(the bot's own *"is my data private?"* answer is generated from the same facts).

## What leaves your machine

| Data | Sent to | When |
| --- | --- | --- |
| The user's message, the last ≤10 messages from the past 24h, saved contact names, saved facts | Google Gemini API (or **OpenAI**, for users who chose it and only if you enabled it) | ordinary conversation messages |
| Photos, voice notes, PDFs the user sends | Meta (download) → selected AI provider; see recording transcription below | when sent |
| Message text | Gemini embeddings API | stored for semantic search ("what did we say about X") |
| Email subjects/bodies, calendar events | Gemini | only when the user asks about mail/calendar |
| **Proactive mode only:** new-email sender + subject + Gmail's short snippet; upcoming calendar events | Gemini | opt-in, per user, polled in the background; detection and wording only |
| Search queries | Gemini with Google Search grounding | *"what happened with X"* |
| Tracking numbers | Ship24 | package tracking |
| City names | Open-Meteo | weather |
| Ticker symbols | Yahoo Finance | quotes |
| Reminder/alert text | Meta WhatsApp Cloud API | every outbound message |

If you enable OpenAI (`OPENAI_API_KEY` + `OPENAI_MODEL`), those users' content goes to OpenAI instead. Text-generation requests
use `store: false` (a request, not a guarantee); Audio transcription has no such parameter. Embeddings and image generation still use Gemini. The `/privacy` page and the welcome message name OpenAI automatically.
Check OpenAI's own data-use terms for the account your key belongs to.

Google states API data is not used to train its models on paid/billing-enabled projects, but that is
Google's policy, not something this software enforces — read the terms of the plan your key is on.
**On the unpaid tier Google may use submitted content to improve its products and humans may review it**,
which contradicts the Limited Use statement on `/privacy`: use a billing-enabled project if anyone connects Google.

## What is stored (one SQLite file, `DB_PATH`)

Messages and their embeddings, contacts, remembered facts, reminders, saved link snapshots, drafts,
watches, tracked packages, kids' schedules, proactive settings/VIP list, and **Google OAuth tokens
(encrypted with `TOKEN_ENCRYPTION_KEY`, Fernet)**. Proactive bookkeeping keeps message *IDs*,
event summaries and a short log of what was sent.

- **Not encrypted at rest** apart from the Google tokens: protect the disk/backups. Backups contain
  every user's conversation history.
- **Retention:** conversation history is kept until the user says *"delete my history"* (or you delete
  the DB). Proactive bookkeeping tables are pruned after 60 days automatically.
- Each user can ask *"show me what you have on me"* (counts only) and *"delete my history"*.

## Who can see what

- Users are isolated from each other: tables are scoped by user, and operations that take an ID verify ownership.
- The admin dashboard shows counts, active reminders and usage — **not message or email content** —
  and every admin view or action is written to `admin_audit_log`.
- You, with shell access to the host, *can* read the database. Don't tell users otherwise.
- The dashboard is off until `ADMIN_ALLOWED_EMAIL` is set, is served only on `ADMIN_HOST`, and should
  sit behind Cloudflare Access with `CF_ACCESS_TEAM_DOMAIN`/`CF_ACCESS_AUD` set so the bot verifies
  Access's signed token. Keep it off the public hostname.

## Prompt-injection and approvals

Email and web content is treated as untrusted. The bot never sends email without explicit user
approval, and proactive messages only *tell* the user — any suggested action goes through the same
confirmation step.

## Before you onboard other people

1. Tell them what's in the table above, in plain language.
2. Publish a privacy page and terms — the bot serves `/privacy` and `/terms`, English + Hebrew (set
   `ADMIN_CONTACT_EMAIL`); Google's OAuth consent screen requires a privacy page. See
   [TERMS_TEMPLATE.md](./TERMS_TEMPLATE.md). Hebrew version of this guide: [PRIVACY_FOR_OPERATORS.he.md](./PRIVACY_FOR_OPERATORS.he.md).
3. Decide how long you keep backups, and delete a leaver's data (`delete_history` + remove the user).
4. If you are subject to GDPR or similar, this is a family/personal-use tool by design; running it
   for the public is a different compliance problem this project does not solve.

## Recording transcription

Forwarded recordings and explicitly armed direct recordings use a read-only path.
Meta supplies the recording; the selected Gemini provider receives audio only with
transcription instructions, without chat history, contacts or tool definitions.
For OpenAI, audio goes to the Audio transcription API, then the literal transcript
goes to the configured Responses model for punctuation and summary. Only the
Responses call sends `store=false`; the Audio endpoint has no such parameter.
There is no silent fallback between providers. Provider storage/data-use terms still
apply; app-level in-memory processing is not a promise of provider zero retention.

The app does not save audio files. Literal text, readable replies and summaries are
stored in the ordinary message history and removed by history/account deletion.
Pending one-shot state stores only user ID and expiry; it expires in ten minutes,
and is removed on consumption, cancellation, history deletion or account deletion.
Failure/timing diagnostics contain fixed reason codes and durations, not speech,
model responses or provider exception details. Transcription accuracy is not guaranteed.

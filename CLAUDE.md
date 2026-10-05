# CLAUDE.md - Pika, a self-hosted WhatsApp assistant (Gemini)

Read this first. It tells you what to do depending on what the person asked for. Details live in
[README.md](./README.md) and [docs/](./docs); do not duplicate them, follow them.

**Never ask for, print, paste or commit secrets** (WhatsApp token, app secret, Gemini key, Google client secret,
`TOKEN_ENCRYPTION_KEY`). The person edits `.env` themselves; you tell them which variable goes where. Never read
`.env` back into the conversation.

## A. The person wants to INSTALL / TRY it

Work in this order and stop at the first failure (each step is checkable):

1. **Try without WhatsApp (2 minutes).** Needs only a Gemini key. Python 3.12+.
   ```bash
   pip install -r requirements.txt
   echo "GEMINI_API_KEY=their-key" > .env      # they type the key, you do not
   python scripts/chat.py
   ```
   `chat.py` runs the real message pipeline against a throwaway sandbox DB. Calendar/Gmail/Drive are unavailable there.
2. **Real setup.** `cp .env.example .env`, then ask them to fill it in. Explain each variable from `.env.example`.
   Meta/WhatsApp is the long part: walk them through [docs/SETUP_META.md](./docs/SETUP_META.md) (it is the guide;
   menu names drift, say so). Generate `TOKEN_ENCRYPTION_KEY` with the command in `.env.example`.
3. **Validate** after every `.env` change: `python scripts/doctor.py` (offline), then
   `python scripts/doctor.py --online --url https://<their-domain>` once the bot is reachable. Fix every `FAIL`. Read the
   `WARN`s aloud to the person - two matter before inviting anyone: `OPERATOR_NAME` / `ADMIN_CONTACT_EMAIL` unset
   (placeholders would show on the public `/privacy` and `/terms` pages) and the Gemini plan (see below).
4. **Run:** Docker `docker compose up -d --build` (then `docker compose run --rm bot python scripts/create_admin.py <number> "<name>"`),
   or `python -m uvicorn src.main:app --host 127.0.0.1 --port 8000`. Create the first admin with
   `python scripts/create_admin.py 972501234567 "Name"` (digits only, international format, no `+`).
5. **Public HTTPS URL** for the webhook (Cloudflare Tunnel is the documented path; the compose file has a `tunnel` profile).
6. **Adding people** needs TWO approvals: Meta's recipient list (while the app is in development mode) AND the bot's
   own allowlist. See "Adding a person" in docs/SETUP_META.md. A message from a number that is not a user is silently ignored.
7. **Templates:** anything sent more than 24 h after the user's last message needs an approved Meta template
   (`reminder_notification`, `package_status_update`, `google_reconnect_needed`, `proactive_update`, `welcome_user`;
   parameter counts in the README table). Mention it; do not fake approval.

### Things to tell the person before they invite anyone else

- They become the **operator**: the data controller, able to technically read the database. `/privacy` and `/terms`
  are served by the bot (English + Hebrew); they must set `OPERATOR_NAME`, `ADMIN_CONTACT_EMAIL` and ideally `PUBLIC_BASE_URL`
  (sends each new user a one-time welcome with the policy links). See [docs/TERMS_TEMPLATE.md](./docs/TERMS_TEMPLATE.md). None of this is legal advice.
- If anyone connects Gmail/Calendar/Drive, the Gemini key must come from a **billing-enabled** project (shows as "Paid" in
  AI Studio). On the free tier Google may use submitted content to improve its products, which contradicts the Limited Use
  statement on `/privacy`. `doctor.py` cannot detect the plan; ask.
- OpenAI is optional. If the person wants it: both `OPENAI_API_KEY` and `OPENAI_MODEL` (no default model name exists), then `doctor.py`. It changes who receives
  users' content; `/privacy` updates itself, but tell the person.
- Google OAuth consent screen left in *Testing* expires refresh tokens every 7 days; publish it to *Production*.
- Replies composed by the code follow `LOCALE` (`he` default, `en` supported; catalogs in `src/locales/`). If they switch,
  also set `WHATSAPP_TEMPLATE_LANGUAGE` and approve the templates in that language; `doctor.py` warns when the two differ.
  The admin dashboard and the model's own classification prompts are still Hebrew.

## B. The person wants to CHANGE the code

**Commands (run before saying "done"):**
```bash
pip install -r requirements-dev.txt
pytest -q                      # ~950 tests, no network or real credentials needed
ruff check .                   # correctness rules only
mypy                           # only the modules listed in pyproject.toml [tool.mypy] files; they must stay clean
```
CI runs all three on Python 3.12/3.13/3.14 plus a Docker smoke test and CodeQL. Add a regression test with every bug fix.

**Layout**
- `src/main.py` FastAPI app (health `/`, `/about`, `/privacy`, `/terms`); `src/webhook_handler.py` the message pipeline
  (allowlist -> classify -> run tool -> send) and most `_handle_*` functions; `src/router.py`, `src/oauth_handler.py`, `src/admin_handler.py`
  (dashboard, fail-closed unless `ADMIN_ALLOWED_EMAIL`); `src/scheduler.py` APScheduler jobs (reminders, packages, proactive);
  `src/proactive.py` the opt-in proactive Gatekeeper; `src/db/` SQLite (`schema.sql` + migrations in `models.py`);
  `src/integrations/` one module per external service; `src/legal_pages.py`; `src/welcome.py`; `src/user_deletion.py`.
- **Every capability is a tool** in `src/tools/` (name, description Gemini reads, JSON schema, handler). Gemini must pick exactly one
  tool per message; `chat` and `unclear` are tools too. Read [docs/ADDING_A_TOOL.md](./docs/ADDING_A_TOOL.md) before adding one. Also add
  the tool to `_CAPABILITY_GROUPS` in `webhook_handler.py` or "what can you do?" silently omits it (a test guards this).
- The tools pipeline runs the tool itself and marks its envelope `tool_executed=True`; the legacy action dispatch in the pipeline
  must never run a tool a second time. Keep it that way if you touch `_classify_text_with_cutover` or the dispatch.
- `src/intent_parser.py` is the older single-call classifier, kept only as a fallback when the tools call fails.
- **AI providers** (`src/ai.py`): Gemini is the default, OpenAI optional (`OPENAI_API_KEY` + `OPENAI_MODEL`), chosen per user. The four LLM
  entry points hand over to the current provider's adapter via a ContextVar. Any new background job that calls the LLM *for a user* must run
  under `with use_user(user):` (or `@for_user`), and a new provider must follow [docs/ADDING_A_PROVIDER.md](./docs/ADDING_A_PROVIDER.md) -
  including naming it on `/privacy`. No silent failover between providers, ever.

**Conventions**
- Match the surrounding style. Comments explain *why* (constraints, past incidents), not *what*.
- All Gemini `generate_content` calls pass `automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)`.
- User data is always scoped by `user_id`; operations taking an id verify ownership. A new table that holds user data needs a
  foreign key to `users` so `scripts/delete_user.py` can remove it (a test fails otherwise).
- Time-dependent tests must pin the clock (`daytime_clock` fixture) - proactive logic has quiet hours.
- Messages from unknown numbers are ignored with no Gemini call; keep that.

**Privacy rules for contributions (this repo is public)**
- Never put real phone numbers, names, tokens, emails, hostnames or paths in code, tests, fixtures or docs. Use `972500000001`-style
  numbers and `example.com`.
- No new data leaving the machine without updating `docs/PRIVACY_FOR_OPERATORS.md` (+ `.he.md`) **and** `src/legal_pages.py`
  (and `LEGAL_PAGES_UPDATED`); `tests/test_legal_pages.py` guards the key statements.
- Do not weaken the approval steps: email is never sent and calendar events are never created without explicit user confirmation, and
  email/web content is treated as untrusted (prompt injection).

**Git:** small commits, tests green first. Security problems go to [SECURITY.md](./SECURITY.md), not public issues.

## C. The person has a QUESTION

Answer from README / docs. Costs: [docs/COSTS.md](./docs/COSTS.md). Failures: [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) and `doctor.py`. Roadmap and
open work: [ROADMAP.md](./ROADMAP.md) and the issues. If something is not documented or you are unsure whether Meta's UI still matches the
guide, say so instead of guessing.

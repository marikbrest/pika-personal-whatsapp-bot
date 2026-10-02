# Troubleshooting

Start with `python scripts/doctor.py --online --url https://<your-domain>` — it checks most of
what is below and names the failing item. Logs: `docker compose logs -f bot`, or
`logs/uvicorn.log` on Windows.

## The bot never replies

| Symptom / log line | Cause | Fix |
| --- | --- | --- |
| Nothing in the log at all when you message it | Meta isn't calling your webhook | Webhook not verified/subscribed: finish [step 4 of the Meta guide](./docs/SETUP_META.md#4-register-the-webhook) and subscribe to `messages`; check the tunnel is up |
| `message from unknown number (...) - ignoring` | You are not a user — unknown numbers are ignored on purpose | `python scripts/create_admin.py <number> "<name>"` (digits only, international format, no `+`) |
| `403` on `POST /webhook` | Signature check failed | `WHATSAPP_APP_SECRET` is missing or is not *this* app's secret |
| Replies stop ~24h after you set up | Meta's temporary access token expired | Create a permanent System User token ([guide, step 6](./docs/SETUP_META.md#6-going-to-production)) |
| Webhook "Verify and save" fails in Meta | Verify token mismatch, or the bot isn't reachable | `python scripts/doctor.py --url https://<your-domain>` shows which; restart the bot after editing `.env` |
| 502 for ~30s after a restart | The tunnel is re-establishing | Wait; it is normal |
| Works, then stops after the machine sleeps | The host suspended the process | Disable sleep on the host (Windows: Settings → Power) |
| `another instance already holds the single-instance lock` | A second copy is already running (the bot refuses to start twice on purpose) | Stop the old process (`docker compose down`, or `scripts/stop_assistant.ps1` on Windows) |

## Messages to family/contacts or alerts never arrive

WhatsApp only lets a business message someone **first** inside a 24-hour window after *their*
last message to you. Outside it, only an approved **template** is delivered.

- Log shows `delivery FAILED ... 131047` ("Re-engagement message") → no approved template covers
  that message, or the recipient hasn't messaged the bot in 24h. Create the four templates in the
  [main README](./README.md#whatsapp-message-templates) and wait for approval.
- A "successful" send that never arrives can be reported *later* by a status webhook — look for
  `status update for ...: failed` lines. The bot retries via template automatically when it can.
- Meta may re-categorise a template from *Utility* to *Marketing* after approving it (higher cost).
  Keep the wording plainly transactional; if it happens anyway, appeal in **Business Support Home →
  Template category updates → Request review** (you have 60 days). Sending works either way.
- While your WhatsApp app is in development mode, you can only message numbers on the **API Setup →
  To** list.

## Google (Calendar / Gmail / Drive)

- `invalid_grant: Token has been expired or revoked` → the refresh token died. The bot detects this
  daily and sends a reconnect link; open it. If it **keeps happening about weekly**, your OAuth
  consent screen is still in *Testing* — publish it to *Production* (see the
  [README gotcha](./README.md#google-cloud-oauth-setup-gotcha)). This was the single most common
  silent failure in real use.
- Google shows "this app isn't verified" → expected for a personal deployment; Advanced → Go to app.
- Calendar/mail replies say "not connected" → that user hasn't completed the Google link; they say
  *"connect Google"* to get one. In `scripts/chat.py` Google features are always unavailable.
- Redirect URI mismatch → `GOOGLE_REDIRECT_URI` in `.env` must exactly match one registered in Google
  Cloud, including `https://` and the `/oauth/callback` path.

## Proactive updates aren't arriving

Check in this order — each one silently produces "nothing" rather than an error:

1. **Proactive mode is opt-in and off by default.** Message the bot *"turn on proactive mode"* and
   check *"proactive mode status"*.
2. **Google must be connected** for that user (see above) — without it the collectors have no data.
3. **Quiet hours** (default 22:30–07:00), a **"busy until..." status**, or the **daily cap** (default 6)
   are holding messages. Quiet-hours/busy messages are delivered later as a digest; cap-blocked ones
   are dropped by design.
4. Outside the 24h window, delivery needs the `proactive_update` template approved (above).
5. The model can decide something isn't worth interrupting you for — that is the point of the feature.

## Admin dashboard

- Always `403` → `ADMIN_ALLOWED_EMAIL` is empty (the dashboard is off by design), you are not on
  `ADMIN_HOST`, or the identity Cloudflare Access reports is not exactly `ADMIN_ALLOWED_EMAIL`.
  With `CF_ACCESS_TEAM_DOMAIN`/`CF_ACCESS_AUD` set, also check the AUD tag is the one of the Access
  application protecting that hostname, and that the server can reach
  `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs`.

## Docker

- `permission denied` on `/data` with a **bind mount** → the container runs as UID 10001. Use the
  named volume from `docker-compose.yml`, or `chown -R 10001 ./data`.
- `docker compose up` complains about a missing `.env` → `cp .env.example .env` first.
- Container shows `unhealthy` → `docker compose logs bot`; usually a missing required variable.

## Other

- Hebrew prints as `?`/crashes in a Windows terminal → use a UTF-8 terminal (`chcp 65001`); the
  bundled scripts already force UTF-8 output.
- Gemini errors / `404` model → the model alias in `src/integrations/gemini.py` changed on Google's
  side; verify the *response body*, not just the HTTP status, after changing it.
- Restored a backup and Google features broke → `TOKEN_ENCRYPTION_KEY` differs from the one that
  encrypted the stored tokens; users must reconnect Google.

Still stuck? Open an issue (redact numbers, tokens and email addresses) or ask in Discussions.

# Security policy

This bot reads private email, calendar and chat content, so security reports matter.

**Please do not open a public issue for a vulnerability.** Use GitHub's
[private vulnerability reporting](https://github.com/marikbrest/pika-personal-whatsapp-bot/security/advisories/new) for this repo instead.

Hardening you should keep when self-hosting: set `CF_ACCESS_TEAM_DOMAIN` + `CF_ACCESS_AUD` so the admin dashboard verifies Cloudflare Access's signed token (otherwise never expose the dashboard host any way except through Access); never commit `.env`; keep the dashboard
behind Cloudflare Access (or equivalent) on a separate host; keep `WHATSAPP_APP_SECRET`
set so webhook signatures are verified; rotate any key that was ever pasted into a chat
or log.

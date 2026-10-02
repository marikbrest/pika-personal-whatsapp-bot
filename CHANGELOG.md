# Changelog

All notable changes to this project. Format follows [Keep a Changelog](https://keepachangelog.com/).

## [0.3.0] - 2026-10-02

### Security
- Admin dashboard fails closed: it is off until `ADMIN_ALLOWED_EMAIL` is set (previously an empty value let any
  Cloudflare Access identity in).
- Optional verification of the signed Cloudflare Access JWT (`CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`), so a forged
  `Cf-Access-Authenticated-User-Email` header no longer grants access.

### Added
- `DEFAULT_TIMEZONE` and `DEFAULT_LOCATION` settings for deployments outside Israel; `doctor.py` checks them and the
  dashboard identity mode.

### Changed
- Dependency updates from Dependabot (google-genai 2.25, apscheduler 3.11, GitHub Actions majors).
- Gemini calls explicitly disable the SDK's automatic function calling (the bot dispatches tools itself), which
  also silences a confusing warning google-genai 2.x prints otherwise.
- Proactive-delivery tests pin the clock; previously ten of them failed whenever the suite ran during the
  default quiet hours (22:30-07:00 Israel time).
- README is explicit that Pika is Hebrew-first (understands English; code-composed replies are Hebrew).

## [0.2.2] - 2026-10-02

### Security
- `cryptography` 49.0.0 → 50.0.2 (clears the last open Dependabot advisory).

## [0.2.1] - 2026-10-02

### Security
- Bumped `cryptography` 43.0.1 → 49.0.0 (it encrypts stored Google tokens), `python-multipart` 0.0.20 → 0.0.32,
  `python-dotenv` 1.0.1 → 1.2.3 and `pytest` → 9.1.1 to clear 27 open Dependabot advisories (12 high).

## [0.2.0] - 2026-10-02

### Added
- Docker image runs as a non-root user and has a health check.
- `TROUBLESHOOTING.md`, `docs/COSTS.md` (real usage numbers), `docs/PRIVACY_FOR_OPERATORS.md`, `ROADMAP.md`.
- CodeQL scanning, secret scanning with push protection, Dependabot alerts.

### Upgrade note
- Existing Docker volumes created by 0.1.x are owned by root; run `docker compose run --rm --user root bot chown -R 10001 /data` once.

## [0.1.1] - 2026-10-02

### Fixed
- Docker image is now multi-arch (amd64 + arm64), so it runs on Apple Silicon and Raspberry Pi.

## [0.1.0] - 2026-10-02

First public release.

### Added
- WhatsApp assistant (Gemini function-calling) with reminders, persistent "nag until done" reminders,
  Google Calendar / Gmail / Drive with approval flows, weather and markets, web search, package
  tracking, change watches, image generation and editing, saved links, long-term memory.
- Opt-in proactive mode: LLM situation assessment plus a deterministic delivery policy (quiet hours,
  daily cap, VIP list, busy status, held-for-later digests); meeting briefings; cost guard.
- Multi-user isolation, admin dashboard (counts only, audit-logged), privacy tools.
- `scripts/chat.py` terminal sandbox (no WhatsApp/Meta needed), `scripts/doctor.py` setup checker,
  `scripts/create_admin.py`, cross-platform `scripts/backup_db.py`.
- Docker image and compose file, with an optional Cloudflare Tunnel profile.
- Docs: Meta setup guide, "adding a tool" guide.

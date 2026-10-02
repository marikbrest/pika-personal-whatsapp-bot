"""
Server entry point.
Run with: uvicorn src.main:app --host 127.0.0.1 --port 8000
(loopback only - cloudflared proxies to this port from the same machine;
see scripts/start_assistant.ps1 for why 0.0.0.0 is not safe here)
"""
import socket
import sys

# Guards against UnicodeEncodeError crashes on Windows when output is
# redirected to a file (the default cp1252 codec cannot encode Hebrew).
# print() calls should still be English, but this is a safety net in case
# non-ASCII text is added later.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from src.admin_handler import router as admin_router
from src.config import ADMIN_CONTACT_EMAIL
from src.db.models import init_db
from src.oauth_handler import router as oauth_router
from src.scheduler import start_scheduler_loop
from src.webhook_handler import router as webhook_router

# 2026-09-26: single-instance guard, found necessary after a REAL live
# incident - two full copies of this process ended up running at once
# (both from ONE Start-Process call in scripts/start_assistant.ps1, same
# PID creation timestamp down to the second; the Windows-level mechanism
# was never pinned down despite real investigation, but the effect was
# reproduced twice in a row). Only one of the two ever actually held
# 127.0.0.1:8000 - the other was a fully live orphan nobody could see from
# the webhook traffic or from watching the "winning" process's own log,
# because start_scheduler_loop() below runs at IMPORT time, unconditionally,
# before uvicorn's own socket bind even happens - so the loser still got a
# fully independent scheduler and used it to send at least one real
# WhatsApp message (a Google-reconnect alert) with no trace anywhere an
# operator would think to look.
#
# _SINGLE_INSTANCE_LOCK_PORT is bound synchronously via a plain socket
# (not through uvicorn/asyncio - whatever let two processes coexist on
# 8000 clearly isn't reliable) and held for the process's entire lifetime
# via this module-level reference, which keeps the OS-level exclusive bind
# alive. A second process attempting the same bind gets a normal,
# unambiguous OSError and exits immediately, before the scheduler (or
# anything else) ever starts - "the port is taken" is the ONE guarantee a
# plain synchronous TCP bind still gives on this box, even though the
# app's real port apparently didn't.
_SINGLE_INSTANCE_LOCK_PORT = 47001
try:
    _instance_lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _instance_lock_socket.bind(("127.0.0.1", _SINGLE_INSTANCE_LOCK_PORT))
    _instance_lock_socket.listen(1)
except OSError:
    print(
        f"[main] another instance already holds the single-instance lock "
        f"(127.0.0.1:{_SINGLE_INSTANCE_LOCK_PORT}) - refusing to start a "
        f"second one. If this is wrong (e.g. after a genuine crash left a "
        f"stale process), stop it and restart."
    )
    sys.exit(1)

app = FastAPI(title="Personal Assistant — WhatsApp")

init_db()

app.include_router(webhook_router)
app.include_router(oauth_router)
app.include_router(admin_router)

# Starts the background reminder check (every 60 seconds) - PRD section 12.2
_scheduler = start_scheduler_loop()


@app.get("/")
async def health_check():
    """Basic liveness check. Not part of the webhook; handy for verifying the tunnel reaches the server."""
    return {"status": "ok"}


def _about_html() -> str:
    contact = ADMIN_CONTACT_EMAIL or "(set ADMIN_CONTACT_EMAIL in .env)"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Personal WhatsApp Assistant</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{font-family:sans-serif;max-width:640px;margin:40px auto;padding:0 16px;line-height:1.6}}</style>
</head>
<body>
<h1>Personal WhatsApp Assistant</h1>
<p>A private WhatsApp bot for one family - reminders, calendar and email management,
and proactive updates. Not a public app and not open for sign-up - it only serves the
family members the admin has personally added.</p>
<p>Questions: <a href="mailto:{contact}">{contact}</a></p>
<p><a href="/privacy">Privacy policy</a></p>
</body>
</html>"""


def _privacy_html() -> str:
    contact = ADMIN_CONTACT_EMAIL or "(set ADMIN_CONTACT_EMAIL in .env)"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Privacy Policy - Personal WhatsApp Assistant</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{font-family:sans-serif;max-width:640px;margin:40px auto;padding:0 16px;line-height:1.6}}</style>
</head>
<body>
<h1>Privacy Policy</h1>
<p>This Personal WhatsApp Assistant is a private, non-public bot serving only the family
members its admin has personally invited.</p>
<ul>
<li>Every conversation with the bot is private - no other user, including the admin, sees
the content of your messages or emails.</li>
<li>The admin can see message counts and active reminders only - never conversation or
email content - and every admin view is recorded in an audit log.</li>
<li>If a user enables "proactive mode" (off by default), the bot reads calendar/email
content in the background to decide what's worth surfacing. That content is sent to an
AI model for detection and wording only - no human ever sees it.</li>
<li>Google data (Calendar/Gmail) is stored only to power the features the user asked for,
and is never shared with a third party.</li>
<li>Any user can ask at any time to see what's stored about them, or to delete their
conversation history.</li>
</ul>
<p>Questions or data-deletion requests: <a href="mailto:{contact}">{contact}</a></p>
</body>
</html>"""


@app.get("/about", response_class=HTMLResponse)
async def about_page():
    """Google OAuth consent screen homepage requirement - needed to publish the app to production so refresh tokens stop expiring every 7 days (Testing-mode limitation)."""
    return _about_html()


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page():
    """Google OAuth consent screen privacy-policy requirement - same reason as about_page. Content mirrors webhook_handler._PRIVACY_EXPLANATION; keep both in sync if either changes."""
    return _privacy_html()


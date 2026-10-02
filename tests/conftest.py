"""
Shared pytest fixtures.

IMPORTANT ordering constraint: src/config.py reads every secret via
os.getenv() into module-level constants at import time. That means the
dummy env vars below must be set BEFORE anything under src/ is imported -
including by test modules. pytest guarantees conftest.py is executed before
it collects/imports any test file in the same directory tree, so this file
(not a fixture inside it) is the only safe place to do it.

None of these values are real credentials. They exist only so that code
paths which would otherwise raise RuntimeError("... missing from .env")
(_fernet(), build_auth_url(), etc.) have something syntactically valid to
work with - a Fernet key that decrypts what it encrypts, a webhook secret
that HMACs consistently within a test run.

~~~ WHY EVERY KEY BELOW IS SET, NOT LEFT ABSENT ~~~
This repo's real, production .env sits right next to it in the same working
directory, and src.config calls dotenv.load_dotenv() with no path argument,
which searches upward from the CWD and loads whatever it finds -
load_dotenv()'s default override=False means it will NOT clobber a key
already present in os.environ, but WILL happily fill in any key this file
leaves unset with the real production value.

That is exactly what happened on 2026-09-13: this file deliberately left
ZABBIX_API_TOKEN/UNIFI_API_KEY/SHIP24_API_KEY unset, intending "unset" to
mean "not configured" for the not-configured test cases - but on THIS
machine those keys are very much configured for the real bot, so
load_dotenv() filled in the real ones, and one test that forgot to mock the
network call went on to make one real POST to Ship24 (consuming one of the
100 calls/month quota) using the real key, and then wrote a real row into
production's api_usage_log via the real, unmocked DB_PATH (see the DB_PATH
placeholder below - that same test also had no db_path fixture). Caught
immediately after the run, the API call itself can't be undone, but nothing
else about it (no other table, no other row) was touched. Never again: every
optional integration credential is forced blank here, unconditionally, so a
"not configured" test means exactly that regardless of what the real .env
next to the test run happens to contain.
"""
import os
import tempfile

from cryptography.fernet import Fernet

os.environ.setdefault("WHATSAPP_APP_SECRET", "test-whatsapp-app-secret")
os.environ.setdefault("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "test-webhook-verify-token")
os.environ.setdefault("WHATSAPP_ACCESS_TOKEN", "test-whatsapp-access-token")
os.environ.setdefault("WHATSAPP_PHONE_NUMBER_ID", "000000000000")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-api-key")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-google-client-secret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "https://assistant.your-domain.example/oauth/callback")
os.environ.setdefault("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("ADMIN_HOST", "admin.your-domain.example")
os.environ.setdefault("ADMIN_ALLOWED_EMAIL", "admin@example.com")

# Forced blank, not merely "left unset" - see the incident note above. Empty
# string is falsy, so every "if not X: raise NotConfiguredError" check in
# these integrations fires exactly as if .env genuinely had nothing there.
for _optional_integration_key in (
    "ZABBIX_API_URL", "ZABBIX_API_TOKEN", "UNIFI_HOST", "UNIFI_API_KEY", "SHIP24_API_KEY",
):
    os.environ[_optional_integration_key] = ""

# A second, independent safety net for the same incident: any test that
# forgets to request the db_path fixture must still be structurally
# incapable of writing to the real ./data/assistant.db - not just "usually
# fine because tests remember to ask for db_path". Every src.db.models
# function reads DB_PATH fresh from the module on each call (see db_path's
# own docstring), so forcing it here, before models is ever imported, covers
# every test in the session by default; db_path below still overrides it
# per-test with a fresh, empty file.
_FALLBACK_DB_PATH = os.path.join(tempfile.mkdtemp(prefix="wa_bot_test_fallback_"), "should_not_be_written_to.db")

import hashlib
import hmac
import json
import sqlite3
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.admin_handler import router as admin_router
from src.db import models
from src.webhook_handler import router as webhook_router

# Actually apply the fallback path declared above, now that `models` is
# importable. Any test that skips the db_path fixture gets this harmless,
# empty, session-wide file instead of silently falling through to whatever
# src.config.DB_PATH resolved to (the real production DB on this machine).
models.DB_PATH = _FALLBACK_DB_PATH

WHATSAPP_APP_SECRET = os.environ["WHATSAPP_APP_SECRET"]
ADMIN_HOST = os.environ["ADMIN_HOST"]
ADMIN_ALLOWED_EMAIL = os.environ["ADMIN_ALLOWED_EMAIL"]


@pytest.fixture()
def db_path(tmp_path, monkeypatch):
    """
    Points src.db.models at a fresh, empty SQLite file for this test only and
    builds the real schema (schema.sql + every migration in _run_migrations)
    against it - the same call production makes at startup, so the test
    schema can never silently drift from the real one.

    Patches models.DB_PATH rather than the DB_PATH env var: DB_PATH is
    re-read as a plain module-global on every models.get_connection() call
    (not captured into a closure at import time), so monkeypatch.setattr on
    the module attribute is both sufficient and safely undone after the test
    even though config.DB_PATH itself was already bound at process start.
    """
    path = str(tmp_path / "test_assistant.db")
    monkeypatch.setattr(models, "DB_PATH", path)
    models.init_db()
    return path


@pytest.fixture()
def make_user(db_path):
    """
    Inserts a user row directly (bypassing the webhook's normal
    admin-invite-only creation path, which is exactly what a test setting up
    fixture data should do) and returns its id. Kwargs override any column.
    """

    def _make(
        whatsapp_number: str = "972500000001",
        display_name: str = "Test User",
        is_admin: bool = False,
        is_active: bool = True,
        timezone: str = "Asia/Jerusalem",
    ) -> int:
        conn = models.get_connection()
        try:
            cur = conn.execute(
                "INSERT INTO users (whatsapp_number, display_name, timezone, is_admin, is_active) "
                "VALUES (?, ?, ?, ?, ?)",
                (whatsapp_number, display_name, timezone, int(is_admin), int(is_active)),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    return _make


@pytest.fixture()
def client(db_path):
    """
    A minimal FastAPI app wired to only the routers under test - NOT
    src.main.app. src.main calls init_db() and start_scheduler_loop() (which
    starts a real APScheduler background loop - reminder checks every 60s,
    an hourly package-tracking poll, a daily Google-token health check that
    would try to reach Google) as a side effect of being imported. Importing
    src.main from a test would start all of that against whatever DB_PATH
    happens to be set, for every test in the session. Building the app
    directly from the routers avoids that entirely while still exercising
    the real route handlers, the real signature/host/header checks, and the
    real FastAPI request/response cycle.
    """
    app = FastAPI()
    app.include_router(webhook_router)
    app.include_router(admin_router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_real_background_reactions(monkeypatch):
    """
    Batch 11 (2026-09-14) added a background-thread reaction pick+send
    (src.webhook_handler._react_to_message_in_background) on every real
    message _process_single_message handles. Autouse here so every existing
    or future test that posts to /webhook doesn't silently fire a real
    (billed, network-dependent, non-deterministic) Gemini call on a
    background thread it never asked for or mocked - found live during
    batch 11's own test run (every webhook-flow test started printing a
    real "API key not valid" error from a background thread, harmless only
    because exceptions there are swallowed, not because nothing happened).
    A test that wants to exercise the reaction logic itself imports and
    calls the real function directly (see tests/test_message_reactions.py) -
    unaffected by this patch, since it holds its own reference to the real
    function rather than going through the module attribute this patches.
    """
    monkeypatch.setattr("src.webhook_handler._react_to_message_in_background", lambda *a, **k: None)


def sign_payload(raw_body: bytes, secret: str = WHATSAPP_APP_SECRET) -> str:
    """Computes a valid X-Hub-Signature-256 header value for a webhook body,
    the same way Meta does - so tests can produce requests the app's own
    _verify_signature() accepts."""
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def whatsapp_text_payload(from_number: str, text: str, message_id: str = "wamid.TEST1") -> dict[str, Any]:
    """A minimal, realistic WhatsApp Cloud API webhook body for one inbound
    text message - the shape the code in webhook_handler.py actually parses
    (entry -> changes -> value -> messages)."""
    return {
        "entry": [
            {
                "id": "0",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "id": message_id,
                                    "from": from_number,
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ]
    }


def post_webhook(client: TestClient, payload: dict) -> Any:
    """POSTs a webhook payload with a correctly computed signature, matching
    exactly how FastAPI/httpx will actually serialize the JSON body - signing
    anything other than the literal bytes sent would produce a signature the
    server correctly rejects."""
    raw_body = json.dumps(payload).encode("utf-8")
    return client.post(
        "/webhook",
        content=raw_body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": sign_payload(raw_body)},
    )

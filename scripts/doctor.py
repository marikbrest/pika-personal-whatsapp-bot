"""
Checks your Pika setup and tells you what is missing or broken.

    python scripts/doctor.py                       # offline checks (config, DB, keys)
    python scripts/doctor.py --online              # + Gemini key and WhatsApp token
    python scripts/doctor.py --url https://assistant.example.com   # + public webhook handshake

Exit code is 0 only when nothing failed (warnings are fine).
"""
import argparse
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

OK, WARN, FAIL = "  OK  ", " WARN ", " FAIL "
results: list[tuple[str, str, str]] = []


def check(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))
    print(f"[{status}] {name}" + (f" - {detail}" if detail else ""))


def env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def check_required_env() -> None:
    for name, hint in [
        ("WHATSAPP_ACCESS_TOKEN", "Meta > WhatsApp > API Setup (use a permanent System User token for production)"),
        ("WHATSAPP_PHONE_NUMBER_ID", "Meta > WhatsApp > API Setup"),
        ("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "any random string; the same value goes into Meta's webhook config"),
        ("WHATSAPP_APP_SECRET", "Meta app > Settings > Basic > App Secret (webhook signatures are rejected without it)"),
        ("GEMINI_API_KEY", "https://aistudio.google.com/"),
        ("TOKEN_ENCRYPTION_KEY", 'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'),
    ]:
        check(OK if env(name) else FAIL, name, "" if env(name) else f"not set - {hint}")

    key = env("TOKEN_ENCRYPTION_KEY")
    if key:
        try:
            from cryptography.fernet import Fernet

            Fernet(key.encode())
            check(OK, "TOKEN_ENCRYPTION_KEY is a valid Fernet key")
        except Exception:
            check(FAIL, "TOKEN_ENCRYPTION_KEY is a valid Fernet key", "malformed - regenerate it (existing Google tokens become unreadable)")

    google = [env("GOOGLE_CLIENT_ID"), env("GOOGLE_CLIENT_SECRET")]
    if all(google):
        uri = env("GOOGLE_REDIRECT_URI")
        if not uri or "your-domain.example" in uri:
            check(FAIL, "GOOGLE_REDIRECT_URI", "still the placeholder - set your real https://<domain>/oauth/callback")
        else:
            check(OK, "Google OAuth configured", uri)
    elif any(google):
        check(FAIL, "Google OAuth", "only one of GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET is set")
    else:
        check(WARN, "Google OAuth not configured", "Calendar/Gmail/Drive features will be unavailable (optional)")

    tz = env("DEFAULT_TIMEZONE") or "Asia/Jerusalem"
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(tz)
        check(OK, "DEFAULT_TIMEZONE", tz)
    except Exception:
        check(FAIL, "DEFAULT_TIMEZONE", f"{tz!r} is not a valid IANA timezone (e.g. Europe/London)")

    if env("ADMIN_ALLOWED_EMAIL"):
        if env("CF_ACCESS_TEAM_DOMAIN") and env("CF_ACCESS_AUD"):
            check(OK, "Admin dashboard identity", "verifying the signed Cloudflare Access JWT")
        else:
            check(WARN, "Admin dashboard identity", "trusting the Cf-Access email header - safe ONLY if every request to ADMIN_HOST goes through Cloudflare Access; set CF_ACCESS_TEAM_DOMAIN + CF_ACCESS_AUD to verify the signed token instead")
    else:
        check(WARN, "Admin dashboard", "off (ADMIN_ALLOWED_EMAIL not set) - optional")

    for name in ("ADMIN_HOST", "ADMIN_CONTACT_EMAIL"):
        v = env(name)
        if not v or "your-domain.example" in v:
            check(WARN, name, "not set - the admin dashboard and /about page need it (optional)")
        else:
            check(OK, name, v)


def check_database() -> None:
    path = env("DB_PATH") or os.path.join(ROOT, "data", "assistant.db")
    folder = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(folder):
        check(WARN, "Database folder", f"{folder} does not exist yet (it is created on first run)")
        return
    if not os.access(folder, os.W_OK):
        check(FAIL, "Database folder is writable", folder)
        return
    check(OK, "Database folder is writable", folder)
    if os.path.exists(path):
        try:
            conn = sqlite3.connect(path)
            admins = conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND is_active = 1").fetchone()[0]
            conn.close()
            if admins:
                check(OK, "At least one admin exists", f"{admins} admin(s)")
            else:
                check(FAIL, "At least one admin exists", 'none - run: python scripts/create_admin.py <number> "<name>"')
        except sqlite3.Error as e:
            check(WARN, "Database readable", str(e))
    else:
        check(WARN, "Database", 'not created yet - run: python scripts/create_admin.py <number> "<name>"')


def check_online() -> None:
    import httpx

    key = env("GEMINI_API_KEY")
    if key:
        try:
            r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models", params={"key": key}, timeout=15)
            check(OK if r.status_code == 200 else FAIL, "Gemini API key", "" if r.status_code == 200 else f"HTTP {r.status_code} - key rejected or API not enabled")
        except httpx.HTTPError as e:
            check(WARN, "Gemini API key", f"could not reach Google ({e.__class__.__name__})")
    token, phone_id = env("WHATSAPP_ACCESS_TOKEN"), env("WHATSAPP_PHONE_NUMBER_ID")
    if token and phone_id:
        try:
            r = httpx.get(
                f"https://graph.facebook.com/v21.0/{phone_id}",
                params={"fields": "display_phone_number,verified_name"},
                headers={"Authorization": f"Bearer {token}"}, timeout=15,
            )
            if r.status_code == 200:
                d = r.json()
                check(OK, "WhatsApp token + phone number ID", f"{d.get('display_phone_number')} ({d.get('verified_name')})")
            else:
                msg = (r.json().get("error", {}) or {}).get("message", "") if r.headers.get("content-type", "").startswith("application/json") else ""
                check(FAIL, "WhatsApp token + phone number ID", f"HTTP {r.status_code} {msg} - temporary tokens expire after ~24h")
        except httpx.HTTPError as e:
            check(WARN, "WhatsApp token", f"could not reach Meta ({e.__class__.__name__})")


def check_public_url(base: str) -> None:
    import httpx

    base = base.rstrip("/")
    try:
        r = httpx.get(base + "/", timeout=15)
        check(OK if r.status_code == 200 else FAIL, "Public URL reaches the bot", f"GET / -> HTTP {r.status_code}")
    except httpx.HTTPError as e:
        check(FAIL, "Public URL reaches the bot", f"{e.__class__.__name__} - is the tunnel up and the bot running?")
        return
    verify = env("WHATSAPP_WEBHOOK_VERIFY_TOKEN")
    if verify:
        try:
            r = httpx.get(base + "/webhook", params={"hub.mode": "subscribe", "hub.verify_token": verify, "hub.challenge": "pika-doctor"}, timeout=15)
            if r.status_code == 200 and r.text.strip() == "pika-doctor":
                check(OK, "Webhook verification handshake", "Meta's callback check will succeed")
            else:
                check(FAIL, "Webhook verification handshake", f"HTTP {r.status_code} - the verify token the *running* bot loaded differs from .env, or /webhook is routed elsewhere")
        except httpx.HTTPError as e:
            check(FAIL, "Webhook verification handshake", e.__class__.__name__)
    r = None
    try:
        r = httpx.get(base + "/privacy", timeout=15)
        check(OK if r.status_code == 200 else WARN, "/privacy page (needed by Google's consent screen)", f"HTTP {r.status_code}")
    except httpx.HTTPError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a Pika setup.")
    parser.add_argument("--online", action="store_true", help="also test the Gemini key and WhatsApp token over the network")
    parser.add_argument("--url", help="public base URL to test (tunnel/reverse proxy), e.g. https://assistant.example.com")
    args = parser.parse_args()

    if not os.path.exists(os.path.join(ROOT, ".env")):
        check(WARN, ".env file", "not found - copy .env.example to .env (environment variables are still checked)")
    check_required_env()
    check_database()
    if args.online:
        check_online()
    if args.url:
        check_public_url(args.url)

    failed = sum(1 for s, *_ in results if s == FAIL)
    warned = sum(1 for s, *_ in results if s == WARN)
    print(f"\n{len(results) - failed - warned} ok, {warned} warning(s), {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

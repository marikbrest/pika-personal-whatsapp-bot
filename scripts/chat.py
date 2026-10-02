"""
Talk to Pika from your terminal - no WhatsApp, Meta or public URL needed.

Only a Gemini key is required (GEMINI_API_KEY in .env or the environment):

    python scripts/chat.py

It feeds your lines through the *same* message pipeline the WhatsApp webhook uses and
prints what the bot would have sent. Replies, reactions and generated images are
captured locally (images are saved under the sandbox folder). Google-backed features
(Calendar/Gmail/Drive) need a real OAuth setup and will say "not connected" here.

Everything lives in a throwaway sandbox database (never your real one):
  - default: data/sandbox.db  (/data/sandbox.db inside Docker)
  - override with PIKA_SANDBOX_DB

Commands: /reset (wipe the sandbox), /help, /quit
"""
import os
import sys
import time
import uuid

# Windows consoles default to a legacy codepage that cannot print Hebrew.
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

SANDBOX_NUMBER = "972500000001"
SANDBOX_DB = os.environ.get("PIKA_SANDBOX_DB") or (
    "/data/sandbox.db" if os.path.isdir("/data") else os.path.join(ROOT, "data", "sandbox.db")
)
# Always force the sandbox DB - this must never touch a real assistant.db.
os.environ["DB_PATH"] = SANDBOX_DB
os.makedirs(os.path.dirname(SANDBOX_DB), exist_ok=True)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))
if not os.environ.get("TOKEN_ENCRYPTION_KEY"):
    from cryptography.fernet import Fernet

    os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

if not os.environ.get("GEMINI_API_KEY"):
    sys.exit("GEMINI_API_KEY is not set. Put it in .env (see .env.example) or export it, then retry.")

OUT_DIR = os.path.join(os.path.dirname(SANDBOX_DB), "sandbox_images")


def _install_fake_whatsapp():
    """Replace every outbound WhatsApp call with a local printer, before anything imports them."""
    import src.integrations.whatsapp as wa

    def send_text_message(to, body):
        print(f"\npika> {body}\n")
        return True

    def send_text_or_template(to, body, template_name=None, language_code=None, body_params=None, on_permanent_failure=None):
        return send_text_message(to, body)

    def send_reaction(to, message_id, emoji):
        print(f"      [reacted {emoji}]")
        return True

    def send_image_bytes(to, image_bytes, mime_type):
        os.makedirs(OUT_DIR, exist_ok=True)
        ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(mime_type, "bin")
        path = os.path.join(OUT_DIR, f"image-{int(time.time())}.{ext}")
        with open(path, "wb") as f:
            f.write(image_bytes)
        print(f"\npika> [image saved to {path}]\n")
        return True

    wa.send_text_message = send_text_message
    wa.send_text_or_template = send_text_or_template
    wa.send_reaction = send_reaction
    wa.send_image_bytes = send_image_bytes


def main() -> int:
    _install_fake_whatsapp()
    from src.db.models import admin_add_user, get_connection, init_db

    init_db()
    admin_add_user(SANDBOX_NUMBER, "You")
    conn = get_connection()
    try:
        conn.execute("UPDATE users SET is_admin = 1, is_active = 1 WHERE whatsapp_number = ?", (SANDBOX_NUMBER,))
        conn.commit()
    finally:
        conn.close()

    from src.webhook_handler import _process_single_message

    print("Pika sandbox - type a message (Hebrew or English). /help for commands, /quit to exit.")
    print(f"(sandbox database: {SANDBOX_DB})\n")
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("/quit", "/exit"):
            return 0
        if line == "/help":
            print(__doc__)
            continue
        if line == "/reset":
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(SANDBOX_DB + suffix):
                    os.remove(SANDBOX_DB + suffix)
            print("Sandbox wiped - restart the script.")
            return 0
        _process_single_message({
            "id": f"sim-{uuid.uuid4()}",
            "from": SANDBOX_NUMBER,
            "type": "text",
            "timestamp": str(int(time.time())),
            "text": {"body": line},
        })


if __name__ == "__main__":
    sys.exit(main())

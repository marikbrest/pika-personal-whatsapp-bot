"""
One-time welcome message for a newly added user, linking the privacy policy and terms.

A policy nobody is shown protects nobody, so adding a user (by chat or from the dashboard) also sends this once.
It needs PUBLIC_BASE_URL (the bot does not otherwise know its own public address) and, because a brand-new user has
never messaged the bot, falls back to the `welcome_user` template (2 parameters: privacy URL, terms URL) when the
24-hour window is closed - see docs/SETUP_META.md.
"""
from src import config
from src.config import OPERATOR_NAME, PUBLIC_BASE_URL, WHATSAPP_TEMPLATE_LANGUAGE
from src.db.models import get_connection
from src.integrations.whatsapp import send_text_or_template

TEMPLATE_NAME = "welcome_user"


def welcome_text(privacy_url: str, terms_url: str) -> str:
    who = f" של {OPERATOR_NAME}" if OPERATOR_NAME else ""
    return (
        f"👋 הוספו אותך לעוזר האישי{who}.\n"
        "מה חשוב לדעת על המידע שלך: ההודעות שלך נשלחות ל-Gemini של Google"
        + (" (או ל-OpenAI, אם תבחר בו)" if config.OPENAI_API_KEY and config.OPENAI_MODEL else "")
        + " ועוברות דרך WhatsApp של מטא, "
        "ומי שמפעיל את הבוט יכול טכנית לקרוא אותן.\n"
        f"מדיניות פרטיות: {privacy_url}\n"
        f"תנאי שימוש: {terms_url}\n"
        "בכל רגע אפשר לכתוב לי \"תראה לי מה יש לך עליי\" או \"תמחק את ההיסטוריה שלי\"."
    )


def send_welcome_if_needed(user_id: int) -> str:
    """Returns "sent", "already_sent", "skipped_unconfigured", "no_such_user" or "failed". Never raises."""
    if not PUBLIC_BASE_URL:
        return "skipped_unconfigured"
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT whatsapp_number, welcome_sent_at FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return "no_such_user"
        if row["welcome_sent_at"]:
            return "already_sent"

        privacy_url, terms_url = f"{PUBLIC_BASE_URL}/privacy", f"{PUBLIC_BASE_URL}/terms"
        ok = send_text_or_template(
            to=row["whatsapp_number"],
            body=welcome_text(privacy_url, terms_url),
            template_name=TEMPLATE_NAME,
            language_code=WHATSAPP_TEMPLATE_LANGUAGE,
            body_params=[privacy_url, terms_url],
        )
        if not ok:
            return "failed"
        conn = get_connection()
        try:
            conn.execute("UPDATE users SET welcome_sent_at = CURRENT_TIMESTAMP WHERE id = ?", (user_id,))
            conn.commit()
        finally:
            conn.close()
        return "sent"
    except Exception as e:
        print(f"[welcome] could not send the welcome message (non-fatal): {e}")
        return "failed"

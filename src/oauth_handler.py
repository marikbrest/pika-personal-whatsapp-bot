"""
Google OAuth callback endpoint.
Google redirects here after the user approves or denies permissions in the browser.
"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.db.models import get_user_by_id
from src.integrations.google_oauth import decode_state, exchange_code_for_tokens, save_tokens
from src.integrations.whatsapp import send_text_message

router = APIRouter()

_SUCCESS_HTML = """
<html dir="rtl"><body style="font-family: sans-serif; text-align: center; padding-top: 80px;">
<h2>✅ החיבור הצליח!</h2>
<p>אפשר לסגור את החלון הזה ולחזור לוואטסאפ.</p>
</body></html>
"""

_ERROR_HTML = """
<html dir="rtl"><body style="font-family: sans-serif; text-align: center; padding-top: 80px;">
<h2>⚠️ החיבור נכשל</h2>
<p>{message}</p>
<p>אפשר לחזור לוואטסאפ ולנסות שוב.</p>
</body></html>
"""


@router.get("/oauth/callback")
async def oauth_callback(request: Request):
    params = request.query_params
    state = params.get("state")
    code = params.get("code")
    error = params.get("error")

    user_id = decode_state(state) if state else None

    if error:
        # The user clicked "cancel" on the Google consent page, or another error on their side
        if user_id:
            user = get_user_by_id(user_id)
            if user:
                send_text_message(to=user["whatsapp_number"], body="החיבור לגוגל בוטל. אפשר לנסות שוב בכל רגע.")
        return HTMLResponse(_ERROR_HTML.format(message="ההרשאה בוטלה."), status_code=400)

    if user_id is None:
        # Invalid or expired state (older than 10 minutes) - nobody to notify
        return HTMLResponse(_ERROR_HTML.format(message="הקישור פג תוקף. תבקש קישור חדש בוואטסאפ."), status_code=400)

    user = get_user_by_id(user_id)
    if user is None:
        return HTMLResponse(_ERROR_HTML.format(message="משתמש לא נמצא."), status_code=400)

    if not code:
        # No error from Google but also no code - a partial or hand-crafted request to the callback URL
        return HTMLResponse(_ERROR_HTML.format(message="בקשה לא תקינה."), status_code=400)

    try:
        credentials = exchange_code_for_tokens(code)
        save_tokens(user_id, credentials)
    except Exception as e:
        print(f"[oauth] token exchange failed for user_id={user_id}: {e}")
        send_text_message(to=user["whatsapp_number"], body="החיבור לגוגל נכשל, תוכל לנסות שוב?")
        return HTMLResponse(_ERROR_HTML.format(message="שגיאה טכנית בחיבור."), status_code=500)

    send_text_message(to=user["whatsapp_number"], body="✅ Gmail והיומן מחוברים בהצלחה! עכשיו אפשר לבקש ממני לקרוא מיילים או לנהל את היומן.")
    return HTMLResponse(_SUCCESS_HTML)

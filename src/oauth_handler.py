"""
Google OAuth callback endpoint.
Google redirects here after the user approves or denies permissions in the browser.
"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.db.models import get_user_by_id
from src.i18n import t
from src.integrations.google_oauth import decode_state, exchange_code_for_tokens, save_tokens
from src.integrations.whatsapp import send_text_message

router = APIRouter()

_PAGE_HTML = """
<html dir="{direction}"><body style="font-family: sans-serif; text-align: center; padding-top: 80px;">
<h2>{title}</h2>
{body}
</body></html>
"""


def _success_page() -> str:
    return _PAGE_HTML.format(
        direction=t("oauth.page_direction"), title=t("oauth.success_title"),
        body=f"<p>{t('oauth.success_body')}</p>",
    )


def _error_page(message: str) -> str:
    return _PAGE_HTML.format(
        direction=t("oauth.page_direction"), title=t("oauth.error_title"),
        body=f"<p>{message}</p>\n<p>{t('oauth.error_footer')}</p>",
    )


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
                send_text_message(to=user["whatsapp_number"], body=t("oauth.cancelled_message"))
        return HTMLResponse(_error_page(t("oauth.cancelled_page")), status_code=400)

    if user_id is None:
        # Invalid or expired state (older than 10 minutes) - nobody to notify
        return HTMLResponse(_error_page(t("oauth.expired_link")), status_code=400)

    user = get_user_by_id(user_id)
    if user is None:
        return HTMLResponse(_error_page(t("oauth.user_not_found")), status_code=400)

    if not code:
        # No error from Google but also no code - a partial or hand-crafted request to the callback URL
        return HTMLResponse(_error_page(t("oauth.bad_request")), status_code=400)

    try:
        credentials = exchange_code_for_tokens(code)
        save_tokens(user_id, credentials)
    except Exception as e:
        print(f"[oauth] token exchange failed for user_id={user_id}: {e}")
        send_text_message(to=user["whatsapp_number"], body=t("oauth.failed_message"))
        return HTMLResponse(_error_page(t("oauth.technical_error")), status_code=500)

    send_text_message(to=user["whatsapp_number"], body=t("oauth.connected_message"))
    return HTMLResponse(_success_page())

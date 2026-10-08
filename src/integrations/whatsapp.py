"""
Basic WhatsApp Cloud API integration.
Phase 1: sending a text message, with retry + exponential backoff (PRD 12.3).
Phase 2: template messages. WhatsApp only allows free-form text to a recipient
who has messaged the bot within the last 24 hours (the "customer service
window"); outside that window, a pre-approved template is the only message
type that can still be delivered. See send_text_or_template.
"""
import time
from typing import Callable

import httpx

from src.config import WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID

GRAPH_API_VERSION = "v21.0"

# PRD 12.3 - retries with backoff: immediate, then after 1s, 5s, 20s
_RETRY_DELAYS_SECONDS = [1, 5, 20]

# A single global client with connection pooling - critical for performance.
# The module-level httpx.post()/get() helpers open a brand new TCP+TLS
# connection on every call. With a persistent client, repeated calls to the
# same host (graph.facebook.com) reuse the existing connection (keep-alive).
#
# keepalive_expiry=120 (instead of the httpx default of only 5 seconds) is
# important for a personal bot: messages arrive sparsely, usually more than
# 5 seconds apart, so the default effectively defeats keep-alive almost every
# time. Two minutes gives the connection a real chance to stay open between
# messages.
_client = httpx.Client(timeout=10.0, limits=httpx.Limits(max_keepalive_connections=5, keepalive_expiry=120.0))

# WhatsApp's error code for "this recipient hasn't messaged the business in
# the last 24h, so a free-form message can't be delivered - only a template
# message is allowed." https://developers.facebook.com/docs/whatsapp/cloud-api/support/error-codes
_OUTSIDE_24H_WINDOW_ERROR_CODE = 131047

# 2026-09-18: message_id (wamid) -> {"to", "template_name", "language_code",
# "body_params"} for a free-text send that send_text_or_template treated as
# successful (200 OK at send time). Found live: 3 reminder sends to contacts
# outside the 24h window ALL returned 200 OK synchronously - the
# _OUTSIDE_24H_WINDOW_ERROR_CODE check in send_text_or_template never
# triggered, so no template fallback happened, and the messages never
# actually reached the recipients. The real rejection (same 131047 code)
# only surfaced later via the async delivery-status webhook, which was, at
# the time, being silently discarded entirely. This dict lets
# handle_delivery_status retry as a template when that late rejection
# arrives - see its own docstring. Per-process, like _credentials_cache in
# google_oauth.py: lost on restart, which only matters for a message still
# in flight at that exact moment - an acceptable, existing tradeoff here.
_pending_24h_window_fallback: dict[str, dict] = {}


def _post_message(payload: dict) -> tuple[bool, dict | None]:
    """
    Shared send+retry logic for any /messages payload (text or template).
    Retries with exponential backoff on rate limits (429), server errors
    (5xx) and network errors. Other client errors (4xx) are not retried -
    they will not fix themselves.

    Returns (success, response_json): on success, response_json is the
    parsed success body (carries messages[0]["id"], the wamid - needed by
    send_text_or_template to track a possible late-surfacing 24h-window
    rejection, see _pending_24h_window_fallback). On failure, it's the
    parsed body of the last failed attempt (None if it never failed, or the
    body wasn't JSON) so a caller can inspect the WhatsApp error code (e.g.
    the 24h-window error) without a second round trip.
    """
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        # Privacy audit (2026-09-26): never print the payload itself - it can
        # carry the real message body/template params, and this log is
        # readable via the admin panel's /admin/logs. Type + recipient is
        # enough to diagnose a missing-credentials config error.
        print(f"[whatsapp] WHATSAPP_ACCESS_TOKEN missing - message not sent (type={payload.get('type')}, to={payload.get('to')})")
        return False, None

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    attempts = 1 + len(_RETRY_DELAYS_SECONDS)
    error_json = None
    for attempt in range(attempts):
        try:
            resp = _client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                try:
                    return True, resp.json()
                except ValueError:
                    return True, None
            retryable = resp.status_code == 429 or resp.status_code >= 500
            print(f"[whatsapp] send failed ({resp.status_code}), attempt {attempt + 1}/{attempts}: {resp.text}")
            try:
                error_json = resp.json()
            except ValueError:
                error_json = None
            if not retryable:
                return False, error_json
        except httpx.HTTPError as e:
            print(f"[whatsapp] network error, attempt {attempt + 1}/{attempts}: {e}")

        if attempt < len(_RETRY_DELAYS_SECONDS):
            time.sleep(_RETRY_DELAYS_SECONDS[attempt])

    print(f"[whatsapp] giving up after {attempts} attempts - message not sent")
    return False, error_json


def send_text_message(to: str, body: str, *, reply_to_message_id: str | None = None) -> bool:
    """
    Sends a free-form text message through the WhatsApp Cloud API.
    Retries with exponential backoff on rate limits (429), server errors (5xx)
    and network errors. Other client errors (4xx) are not retried - they will
    not fix themselves.
    Returns True/False for success; never raises to the caller.
    """
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }
    if reply_to_message_id:
        payload["context"] = {"message_id": reply_to_message_id}
    success, _ = _post_message(payload)
    return success


def send_reaction(to: str, message_id: str, emoji: str) -> bool:
    """
    Sends (or, with emoji="", removes) a WhatsApp emoji reaction on a
    specific message the bot received, identified by its own wamid
    (message_id) - the same thing a person does by long-pressing a message
    and picking an emoji. Best-effort, using the same retry/give-up
    behavior as every other send here (_post_message) - a reaction failing
    must never affect the real reply being sent, since it's purely a nice-
    to-have on top of it, not carrying any information the reply doesn't
    already have.
    """
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "reaction",
        "reaction": {
            "message_id": message_id,
            "emoji": emoji,
        },
    }
    success, _ = _post_message(payload)
    return success


def send_template_message(
    to: str, template_name: str, language_code: str, body_params: list[str] | None = None
) -> bool:
    """
    Sends a pre-approved template message - the only message type WhatsApp
    allows once the recipient hasn't messaged the bot within the last 24
    hours. Prefer send_text_or_template over calling this directly: it tries
    free text first and only falls back to a template when the 24h window is
    actually the reason free text failed.

    body_params are positional {{1}}, {{2}}, ... substitutions for the
    template's Body component, in order - must match exactly what the
    template was approved with in WhatsApp Manager, both in count and in
    what each position means.
    Returns True/False for success; never raises to the caller.
    """
    components = []
    if body_params:
        components.append({
            "type": "body",
            "parameters": [{"type": "text", "text": p} for p in body_params],
        })
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
            "components": components,
        },
    }
    success, _ = _post_message(payload)
    return success


def send_text_or_template(
    to: str, body: str, template_name: str, language_code: str, body_params: list[str],
    on_permanent_failure: Callable[[], None] | None = None,
) -> bool:
    """
    Sends free-form text first; if WhatsApp rejects it specifically because
    the recipient hasn't messaged the bot within the last 24 hours (error
    131047), falls back to the given pre-approved template instead of giving
    up. This is what lets a proactive message (reminder, package update,
    Google reconnect alert) actually reach someone who hasn't opened the chat
    recently - by WhatsApp's own policy, free text alone cannot.

    Any other failure (invalid number, message blocked, template itself not
    yet approved by Meta, etc.) is NOT masked by a second attempt - it still
    returns False, exactly like send_text_message always has. That also
    means this is safe to wire in before a template is approved: until Meta
    approves it, the 24h-window case simply fails the same way it did before
    (the template send fails too), and the moment approval lands, delivery
    starts working with no further code change.

    2026-09-18: found live that WhatsApp does not always reject an
    outside-24h-window free-text send SYNCHRONOUSLY with error 131047 in
    this call's own response - it can return 200 OK here and only report
    the same 131047 rejection later, asynchronously, via a delivery-status
    webhook (3 real reminder sends to contacts outside the window all did
    exactly this - "succeeded" here, never arrived). Since that means a 200
    OK from this function is not a reliable guarantee of delivery, every
    "successful" send here registers its wamid in
    _pending_24h_window_fallback so handle_delivery_status can still retry
    via template if the late rejection shows up.

    on_permanent_failure (2026-09-19): called (with no arguments) if that
    late-surfacing async retry is ALSO unsuccessful - the genuine final
    "this really did not get delivered" moment, which by definition
    happens after this function has already returned, in a later request
    entirely (whenever the status webhook arrives). A synchronous failure
    (this function itself returning False) does NOT invoke it - the
    caller already has that information immediately via the return value
    and can act on it right there, same as always.
    """
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }
    success, response_json = _post_message(payload)
    if success:
        message_id = ((response_json or {}).get("messages") or [{}])[0].get("id")
        if message_id:
            _pending_24h_window_fallback[message_id] = {
                "to": to, "template_name": template_name,
                "language_code": language_code, "body_params": body_params,
                "on_permanent_failure": on_permanent_failure,
            }
        return True

    error_code = (response_json or {}).get("error", {}).get("code")
    if error_code != _OUTSIDE_24H_WINDOW_ERROR_CODE:
        return False

    print(f"[whatsapp] {to} is outside the 24h window - falling back to template {template_name!r}")
    return send_template_message(to, template_name, language_code, body_params)


def handle_delivery_status(message_id: str, status: str, errors: list[dict] | None = None) -> None:
    """
    Called by webhook_handler for every WhatsApp delivery-status callback
    (sent/delivered/read/failed). Clears this message_id's pending-fallback
    tracking (see _pending_24h_window_fallback) on any terminal status, and
    additionally retries via template when the terminal status is
    specifically "failed" with the 24h-window error code - the async
    counterpart of the synchronous check already in send_text_or_template,
    needed because that synchronous check does not always catch this
    rejection (found live 2026-09-18, see send_text_or_template's docstring).

    A message_id not found in the pending map means either an ordinary
    send_text_message/send_template_message call (never registered - those
    already went through the right channel from the start) or a status this
    process already handled - either way, nothing to do.
    """
    pending = _pending_24h_window_fallback.pop(message_id, None)
    if pending is None or status != "failed":
        return

    error_code = (errors[0].get("code") if errors else None)
    if error_code != _OUTSIDE_24H_WINDOW_ERROR_CODE:
        return

    print(
        f"[whatsapp] {pending['to']} - 24h window rejection surfaced asynchronously "
        f"(message_id={message_id}), retrying via template {pending['template_name']!r}"
    )
    retried = send_template_message(pending["to"], pending["template_name"], pending["language_code"], pending["body_params"])
    if not retried:
        on_permanent_failure = pending.get("on_permanent_failure")
        if on_permanent_failure is not None:
            print(f"[whatsapp] {pending['to']} - the template retry ALSO failed, this is a genuine final delivery failure")
            on_permanent_failure()


def upload_media(image_bytes: bytes, mime_type: str) -> str | None:
    """
    Uploads raw bytes to WhatsApp's own media store, returning a media_id for
    use in a subsequent /messages send - the mirror image of download_media's
    two-step process, needed for sending a generated/edited image (which
    only ever exists as bytes in memory, never a hosted URL). Returns None on
    failure; never raises to the caller.
    """
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        print("[whatsapp] WHATSAPP_ACCESS_TOKEN missing - cannot upload media")
        return None

    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/media"
    headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}
    files = {"file": ("image", image_bytes, mime_type)}
    data = {"messaging_product": "whatsapp", "type": mime_type}

    try:
        resp = _client.post(url, headers=headers, files=files, data=data, timeout=30.0)
        if resp.status_code != 200:
            print(f"[whatsapp] media upload failed ({resp.status_code}): {resp.text}")
            return None
        return resp.json().get("id")
    except httpx.HTTPError as e:
        print(f"[whatsapp] network error uploading media: {e}")
        return None


def send_image_bytes(to: str, image_bytes: bytes, mime_type: str) -> bool:
    """
    Uploads raw image bytes and sends them as a WhatsApp image message - used
    by the generate_image/edit_image tools, which only ever have bytes, no
    hosted URL. Returns True/False for success; never raises to the caller.
    """
    media_id = upload_media(image_bytes, mime_type)
    if media_id is None:
        return False

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": {"id": media_id},
    }
    success, _ = _post_message(payload)
    return success


def download_media(media_id: str) -> tuple[bytes, str] | None:
    """
    Downloads a media file (voice message etc.) from the WhatsApp Cloud API.
    This is the officially documented two-step process: first a GET to obtain a
    temporary URL + mime_type, then a second GET (with the same authorization
    token) to download the content itself.

    Returns (audio_bytes, mime_type), or None on failure.
    """
    if not WHATSAPP_ACCESS_TOKEN:
        print("[whatsapp] WHATSAPP_ACCESS_TOKEN missing - cannot download media")
        return None

    headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}

    try:
        meta_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{media_id}"
        meta_resp = _client.get(meta_url, headers=headers)
        if meta_resp.status_code != 200:
            print(f"[whatsapp] media metadata fetch failed ({meta_resp.status_code}): {meta_resp.text}")
            return None

        meta = meta_resp.json()
        file_url = meta.get("url")
        mime_type = meta.get("mime_type", "audio/ogg")
        if not file_url:
            print(f"[whatsapp] media metadata missing url: {meta}")
            return None

        file_resp = _client.get(file_url, headers=headers, timeout=30.0)
        if file_resp.status_code != 200:
            print(f"[whatsapp] media download failed ({file_resp.status_code})")
            return None

        return file_resp.content, mime_type

    except httpx.HTTPError as e:
        print(f"[whatsapp] network error downloading media: {e}")
        return None

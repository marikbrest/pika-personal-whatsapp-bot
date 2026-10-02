"""
Google OAuth2 - connecting Gmail + Calendar through a link-based consent flow.

Since the bot lives in WhatsApp rather than a website, the flow is:
1. The user asks to connect Gmail over WhatsApp
2. The bot builds a Google OAuth link and sends it as a message
3. The user approves in the browser; Google redirects to /oauth/callback
4. The code here exchanges the code for tokens, encrypts and stores them

The OAuth "state" also carries which user_id requested the connection (there is
no conventional web session here). It is encrypted with Fernet, which makes it
both tamper-proof (signed) and self-expiring (10 minutes, via the ttl argument
to Fernet.decrypt).
"""
import json
import os
from datetime import datetime

# Google sometimes returns a different scope set than requested (granular
# consent - the user can untick an individual permission on the consent screen).
# Without this flag oauthlib raises "Scope has changed" and the connection
# fails. We check what was actually granted ourselves.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from cryptography.fernet import Fernet, InvalidToken
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from src.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI, TOKEN_ENCRYPTION_KEY

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.drafts.create",
    "https://www.googleapis.com/auth/calendar.events",
    # Two Drive scopes, deliberately not the broad "drive" scope: readonly
    # covers search (needs to see files the bot didn't create), drive.file
    # covers creation but is limited to files the bot itself creates - it can
    # never read, modify, or delete anything else in the user's Drive.
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]


class NotConnectedError(Exception):
    """The user has not connected their Google account yet."""


class GoogleAuthExpiredError(Exception):
    """
    Authorization was revoked or expired on Google's side (invalid_grant) - for
    example the user manually revoked access at myaccount.google.com/permissions.
    A full reconnect is required.
    """


# 2026-09-19: reasons the Calendar/Gmail/Drive APIs put in an HTTP 403
# response body for ordinary rate-limiting/quota, NOT a genuine auth/
# permission problem - found live, a real reconnect alert fired on two
# consecutive mornings for an account that get_credentials itself (the
# token-refresh classification fixed 2026-09-18) reported perfectly
# healthy both times. Root cause: google_calendar.py/gmail.py/
# google_drive.py each independently caught HttpError from an actual API
# CALL (after get_credentials had already succeeded) and blindly treated
# EVERY 401 *and* 403 as "you need to reconnect" - but Google's own APIs
# reuse 403 for rate-limiting too, which is exactly the kind of transient,
# time-of-day-correlated (shared quota, morning traffic) failure that
# should never trigger a reconnect prompt. See is_genuine_auth_rejection.
_TRANSIENT_403_REASONS = (
    "ratelimitexceeded",
    "userratelimitexceeded",
    "quotaexceeded",
    "dailylimitexceeded",
    "backenderror",
    "rate limit",
)


def is_genuine_auth_rejection(status_code: int, error_text: str) -> bool:
    """
    Shared by google_calendar.py/gmail.py/google_drive.py's HttpError
    handling: a 401 is always a genuine rejection (the access token itself
    was refused). A 403 is only genuine if the error text does NOT
    indicate rate-limiting/quota - those are transient and time-bound, not
    "you need to reconnect". Any other status is never treated as an auth
    rejection by these callers (they only call this for 401/403 in the
    first place).
    """
    if status_code == 401:
        return True
    if status_code == 403:
        text = error_text.lower()
        return not any(reason in text for reason in _TRANSIENT_403_REASONS)
    return False


# RFC 6749 §5.2 error codes Google's token endpoint returns for a genuinely
# permanent rejection (as opposed to a transport/transient failure) - see
# get_credentials's RefreshError handling. invalid_grant/invalid_token cover
# a revoked or expired refresh_token; the rest cover the OAuth app itself
# being disabled, deleted, or misconfigured, which is just as unrecoverable
# without the user reconnecting (or the admin fixing the app config) - none of
# these should be treated as "try again in a few minutes".
_PERMANENT_OAUTH_REJECTION_ERROR_CODES = (
    "invalid_grant",
    "invalid_token",
    "unauthorized_client",
    "access_denied",
    "invalid_client",
    "deleted_client",
)


_STATE_MAX_AGE_SECONDS = 600  # 10 minutes to complete sign-in before the link expires

# Per-process Credentials cache, to avoid reading and decrypting from the DB on
# every single calendar/mail call. Key: user_id. Cleared on server restart,
# which is fine - it is cheap to rebuild.
_credentials_cache: dict[int, Credentials] = {}


def _fernet() -> Fernet:
    if not TOKEN_ENCRYPTION_KEY:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY is missing from .env")
    return Fernet(TOKEN_ENCRYPTION_KEY.encode() if isinstance(TOKEN_ENCRYPTION_KEY, str) else TOKEN_ENCRYPTION_KEY)


def _client_config() -> dict:
    return {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_REDIRECT_URI],
        }
    }


class GoogleNotConfiguredError(RuntimeError):
    """This install has no Google OAuth client - Google features cannot work at all
    (not a per-user "not connected" state). Caught in tools.dispatch."""


def build_auth_url(user_id: int) -> str:
    """Builds a Google OAuth link for a specific user, with encrypted, signed state."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise GoogleNotConfiguredError("GOOGLE_CLIENT_ID/SECRET are missing from .env")

    state = _fernet().encrypt(json.dumps({"user_id": user_id}).encode()).decode()

    flow = Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=GOOGLE_REDIRECT_URI)
    auth_url, _ = flow.authorization_url(
        access_type="offline",  # required in order to receive a refresh_token
        prompt="consent",  # ensures a refresh_token even if the user approved before
        state=state,
    )
    return auth_url


def decode_state(state: str) -> int | None:
    """Decodes the state back into a user_id. Returns None if expired or forged."""
    try:
        payload = _fernet().decrypt(state.encode(), ttl=_STATE_MAX_AGE_SECONDS)
        return json.loads(payload)["user_id"]
    except (InvalidToken, KeyError, ValueError):
        return None


def exchange_code_for_tokens(code: str) -> Credentials:
    """Exchanges the authorization code Google sent for real tokens."""
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=GOOGLE_REDIRECT_URI)
    flow.fetch_token(code=code)
    return flow.credentials


def save_tokens(user_id: int, credentials: Credentials) -> None:
    """Encrypts and stores the tokens in oauth_tokens (upsert by user_id+provider)."""
    from src.db.models import get_oauth_tokens, upsert_oauth_tokens  # late import, avoids a circular import

    fernet = _fernet()
    access_encrypted = fernet.encrypt(credentials.token.encode()).decode()

    refresh_token = credentials.refresh_token
    if not refresh_token:
        # Edge case: Google did not return a refresh_token (despite prompt=consent).
        # If we already have one stored, keep it rather than overwriting with
        # None and breaking the connection.
        existing = get_oauth_tokens(user_id, provider="google")
        if existing is None:
            raise RuntimeError("Google did not return a refresh_token and no existing one is stored")
        refresh_encrypted = existing["refresh_token_encrypted"]
    else:
        refresh_encrypted = fernet.encrypt(refresh_token.encode()).decode()

    upsert_oauth_tokens(
        user_id=user_id,
        provider="google",
        access_token_encrypted=access_encrypted,
        refresh_token_encrypted=refresh_encrypted,
        scope=" ".join(credentials.scopes or SCOPES),
        expires_at=credentials.expiry,
    )

    # Invalidate the stale cache - the next get_credentials call reloads from the DB
    _credentials_cache.pop(user_id, None)


def get_credentials(user_id: int, force_refresh: bool = False) -> Credentials | None:
    """
    Returns valid Credentials for the user, refreshing automatically if the
    access token has expired, and writing the refreshed token back to the DB.
    Returns None if the user has not connected Google at all.

    force_refresh=True exchanges the refresh_token even when the cached access
    token still looks valid. Only the daily health check needs this: an access
    token lasts an hour, so an ordinary call usually proves nothing about whether
    the refresh_token - the part that actually dies - still works.

    Performance: keeps a per-process cache (_credentials_cache) to avoid reading
    and decrypting from the DB on every single calendar/mail call - this matters
    most when several API calls run back to back for the same user. Expiry is
    still checked on every call and refreshed as needed.
    """
    from src.db.models import get_oauth_tokens, upsert_oauth_tokens

    credentials = _credentials_cache.get(user_id)

    if credentials is None:
        row = get_oauth_tokens(user_id, provider="google")
        if row is None:
            return None

        fernet = _fernet()
        access_token = fernet.decrypt(row["access_token_encrypted"].encode()).decode()
        refresh_token = fernet.decrypt(row["refresh_token_encrypted"].encode()).decode()

        expiry = None
        if row["expires_at"]:
            try:
                expiry = datetime.fromisoformat(row["expires_at"])
            except ValueError:
                expiry = None

        credentials = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            scopes=row["scope"].split(" "),
            expiry=expiry,
        )
        _credentials_cache[user_id] = credentials

    if credentials.expired or force_refresh:
        fernet = _fernet()
        try:
            credentials.refresh(GoogleAuthRequest())
        except RefreshError as e:
            # 2026-09-18: RefreshError is NOT only raised when Google
            # explicitly rejects the refresh_token (invalid_grant/
            # invalid_token - genuinely revoked or expired) - google-auth's
            # own _token_endpoint_request already retries transient/
            # retryable failures internally (exponential backoff) before
            # giving up and raising RefreshError for those too, so a
            # RefreshError that survives that internal retry can still be a
            # transport-level failure (a real network/DNS blip, or a
            # transient error from Google's token endpoint), not
            # necessarily a dead token. Found live: a real "sync failed,
            # reconnect" alert fired twice on a long-stable, already-
            # running process (not right after a restart), and a manual
            # re-check minutes/hours later succeeded cleanly both times -
            # the underlying refresh_token was never actually dead.
            #
            # Only treat this as GoogleAuthExpiredError (surfaces the
            # "please reconnect" flow to the user) when the error text
            # itself indicates Google's OAuth layer genuinely rejected the
            # grant - otherwise let the original RefreshError propagate as
            # an ordinary exception, so callers that distinguish "needs
            # reconnect" from "transient, try again" (e.g.
            # scheduler.check_and_send_daily_meetings_summaries, via
            # morning_brief._calendar_section's own generic Exception
            # catch) don't misfire a reconnect alert for what might just be
            # a passing network hiccup.
            error_text = str(e).lower()
            if any(code in error_text for code in _PERMANENT_OAUTH_REJECTION_ERROR_CODES):
                # Drop the cache so a subsequent reconnect isn't shadowed by
                # this broken in-memory credentials object.
                _credentials_cache.pop(user_id, None)
                raise GoogleAuthExpiredError(str(e)) from e
            raise
        # Store the refreshed access token (the refresh_token usually does not change)
        upsert_oauth_tokens(
            user_id=user_id,
            provider="google",
            access_token_encrypted=fernet.encrypt(credentials.token.encode()).decode(),
            refresh_token_encrypted=fernet.encrypt(credentials.refresh_token.encode()).decode(),
            scope=" ".join(credentials.scopes or SCOPES),
            expires_at=credentials.expiry,
        )

    return credentials

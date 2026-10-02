"""
google_oauth.get_credentials's RefreshError classification (2026-09-18 fix)
- found live: a real "sync failed, reconnect" alert fired twice for Yossi
on an already-stable, long-running process (not right after a restart),
and a manual re-check minutes/hours later succeeded cleanly both times -
the refresh_token was never actually dead. Root cause: RefreshError from
google-auth is not ONLY raised for a genuine invalid_grant rejection - a
transport-level failure (network/DNS blip, a transient error from Google's
token endpoint that survives the library's own internal retry) can raise
the exact same exception type. get_credentials now only converts a
RefreshError to GoogleAuthExpiredError (the "you need to reconnect" signal
every caller acts on) when the error text itself indicates a genuine OAuth
rejection - any other RefreshError propagates as-is, so a caller like
scheduler.check_and_send_daily_meetings_summaries (via morning_brief.
_calendar_section's generic Exception catch) treats it as a transient
failure to retry, not a reconnect-worthy one.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from google.auth.exceptions import RefreshError

from src.db.models import upsert_oauth_tokens
from src.integrations.google_oauth import GoogleAuthExpiredError, get_credentials


def _seed_google_token(user_id: int, expired: bool = True):
    """
    A real, Fernet-encrypted oauth_tokens row - get_credentials decrypts
    with the app's own key (conftest.py sets a real, valid
    TOKEN_ENCRYPTION_KEY), same as it would for a real connected user.

    expires_at is deliberately a NAIVE datetime (no tzinfo) - that's what
    the real OAuth callback actually stores (credentials.expiry straight
    from the google-auth library, which represents it as naive UTC
    internally; Credentials.expired compares it against its own naive
    utcnow()). A tz-aware value here would crash google-auth's own
    .expired property with a naive/aware comparison TypeError - not a
    production bug, just a reminder to match the real shape in tests.
    """
    from src.integrations.google_oauth import _fernet

    fernet = _fernet()
    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + (timedelta(hours=-1) if expired else timedelta(hours=1))
    upsert_oauth_tokens(
        user_id=user_id,
        provider="google",
        access_token_encrypted=fernet.encrypt(b"fake-access-token").decode(),
        refresh_token_encrypted=fernet.encrypt(b"fake-refresh-token").decode(),
        scope="https://www.googleapis.com/auth/calendar.events",
        expires_at=expires_at,
    )


@pytest.fixture(autouse=True)
def _clear_credentials_cache():
    """get_credentials keeps a per-process in-memory cache keyed by user_id
    - without clearing it between tests, an earlier test's cached
    Credentials object (or its exhausted mock) would leak into a later one
    with the same user_id."""
    from src.integrations import google_oauth
    google_oauth._credentials_cache.clear()
    yield
    google_oauth._credentials_cache.clear()


def test_invalid_grant_raises_google_auth_expired_error(db_path, make_user):
    user_id = make_user()
    _seed_google_token(user_id, expired=True)

    with patch(
        "google.oauth2.credentials.Credentials.refresh",
        side_effect=RefreshError("invalid_grant: Token has been expired or revoked."),
    ):
        with pytest.raises(GoogleAuthExpiredError):
            get_credentials(user_id, force_refresh=True)


def test_invalid_token_also_raises_google_auth_expired_error(db_path, make_user):
    user_id = make_user()
    _seed_google_token(user_id, expired=True)

    with patch(
        "google.oauth2.credentials.Credentials.refresh",
        side_effect=RefreshError("invalid_token"),
    ):
        with pytest.raises(GoogleAuthExpiredError):
            get_credentials(user_id, force_refresh=True)


def test_a_transient_refresh_error_propagates_as_itself_not_google_auth_expired(db_path, make_user):
    """The core fix: a RefreshError whose text does NOT indicate a genuine
    OAuth rejection (e.g. a network/transport failure) must NOT be
    reclassified as 'you need to reconnect'."""
    user_id = make_user()
    _seed_google_token(user_id, expired=True)

    with patch(
        "google.oauth2.credentials.Credentials.refresh",
        side_effect=RefreshError("Connection aborted: could not reach token endpoint"),
    ):
        with pytest.raises(RefreshError) as exc_info:
            get_credentials(user_id, force_refresh=True)
        assert not isinstance(exc_info.value, GoogleAuthExpiredError)


def test_transient_failure_does_not_poison_the_credentials_cache(db_path, make_user):
    """Unlike a genuine invalid_grant (which drops the cache so a fresh
    reconnect isn't shadowed by stale in-memory credentials), a transient
    failure should leave the cache alone - there's nothing wrong with the
    cached credentials object itself, just this one refresh attempt."""
    from src.integrations import google_oauth

    user_id = make_user()
    _seed_google_token(user_id, expired=True)

    with patch(
        "google.oauth2.credentials.Credentials.refresh",
        side_effect=RefreshError("temporary failure"),
    ):
        with pytest.raises(RefreshError):
            get_credentials(user_id, force_refresh=True)

    assert user_id in google_oauth._credentials_cache


def test_a_successful_refresh_after_a_transient_failure_works_normally(db_path, make_user):
    """Mirrors the real live incident: retrying the exact same call after a
    transient failure should succeed cleanly, proving the token itself was
    never actually dead."""
    user_id = make_user()
    _seed_google_token(user_id, expired=True)

    call_count = {"n": 0}

    def flaky_refresh(self, request):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RefreshError("temporary failure")
        self.token = "refreshed-token"
        self.expiry = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)  # naive, matching google-auth's own convention

    with patch("google.oauth2.credentials.Credentials.refresh", flaky_refresh):
        with pytest.raises(RefreshError):
            get_credentials(user_id, force_refresh=True)

        # a later retry (mirrors the scheduler's own retry, or simply the
        # next poll) succeeds - the refresh_token was fine all along
        creds = get_credentials(user_id, force_refresh=True)
        assert creds is not None
        assert creds.token == "refreshed-token"

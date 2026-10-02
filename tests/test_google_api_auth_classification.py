"""
is_genuine_auth_rejection (2026-09-19 bug fix) - google_calendar.py/
gmail.py/google_drive.py each independently caught HttpError from a real
API call (after get_credentials had already succeeded) and blindly treated
EVERY 401 *and* 403 as "you need to reconnect" - but Google's Calendar/
Gmail/Drive APIs reuse 403 for ordinary rate-limiting/quota too. Found
live: a real reconnect alert fired on two consecutive mornings for an
account get_credentials itself reported perfectly healthy both times -
consistent with a time-of-day-correlated (shared quota, morning traffic)
403 being misclassified as a dead connection.
"""
from googleapiclient.errors import HttpError

from src.integrations.google_calendar import list_events
from src.integrations.google_oauth import GoogleAuthExpiredError, is_genuine_auth_rejection


class _FakeResp:
    def __init__(self, status):
        self.status = status
        self.reason = "reason"


def _http_error(status: int, reason: str) -> HttpError:
    # Matches the real shape of a Google API error body - the top-level
    # "message" is what HttpError._get_reason actually extracts (not the
    # nested "reason" field alone), so it must be populated for str(e) to
    # carry anything meaningful, exactly like a real 403 response does.
    content = (
        f'{{"error": {{"code": {status}, "message": "{reason}", '
        f'"errors": [{{"domain": "usageLimits", "reason": "{reason}", "message": "{reason}"}}]}}}}'
    ).encode()
    return HttpError(_FakeResp(status), content)


def test_401_is_always_a_genuine_rejection():
    assert is_genuine_auth_rejection(401, "anything at all") is True


def test_403_with_no_rate_limit_reason_is_a_genuine_rejection():
    assert is_genuine_auth_rejection(403, "insufficientPermissions") is True


def test_403_rate_limit_exceeded_is_not_a_genuine_rejection():
    assert is_genuine_auth_rejection(403, "userRateLimitExceeded") is False


def test_403_quota_exceeded_is_not_a_genuine_rejection():
    assert is_genuine_auth_rejection(403, "quotaExceeded") is False


def test_403_daily_limit_exceeded_is_not_a_genuine_rejection():
    assert is_genuine_auth_rejection(403, "dailyLimitExceeded") is False


def test_other_status_codes_are_never_a_genuine_rejection():
    assert is_genuine_auth_rejection(500, "backendError") is False
    assert is_genuine_auth_rejection(429, "rate limit") is False


class _FakeExecutor:
    def __init__(self, error):
        self._error = error

    def execute(self):
        raise self._error


def test_list_events_raises_google_auth_expired_on_a_genuine_401(db_path, make_user, monkeypatch):
    import src.integrations.google_calendar as gc

    user_id = make_user()

    class _FakeEvents:
        def list(self, **kwargs):
            return _FakeExecutor(_http_error(401, "authError"))

    class _FakeService:
        def events(self):
            return _FakeEvents()

    monkeypatch.setattr(gc, "_get_calendar_service", lambda uid: _FakeService())

    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("UTC"))

    try:
        list_events(user_id, now, now + timedelta(days=1), "Asia/Jerusalem")
        assert False, "expected GoogleAuthExpiredError"
    except GoogleAuthExpiredError:
        pass


def test_list_events_does_not_raise_google_auth_expired_on_a_rate_limit_403(db_path, make_user, monkeypatch):
    """The core fix: a 403 that is actually rate-limiting must propagate as
    an ordinary HttpError (so callers like _calendar_section's generic
    Exception catch treat it as transient), not force a reconnect prompt."""
    import src.integrations.google_calendar as gc

    user_id = make_user()

    class _FakeEvents:
        def list(self, **kwargs):
            return _FakeExecutor(_http_error(403, "userRateLimitExceeded"))

    class _FakeService:
        def events(self):
            return _FakeEvents()

    monkeypatch.setattr(gc, "_get_calendar_service", lambda uid: _FakeService())

    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("UTC"))

    try:
        list_events(user_id, now, now + timedelta(days=1), "Asia/Jerusalem")
        assert False, "expected the original HttpError to propagate"
    except GoogleAuthExpiredError:
        assert False, "a rate-limit 403 must not be reclassified as an auth rejection"
    except HttpError:
        pass  # correct: propagates as an ordinary, non-auth failure

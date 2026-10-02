"""
src.integrations.shipping - SHIP24_API_KEY is forced blank in conftest.py
(see the incident note there - this exact test is what surfaced the need
for that: on 2026-09-13 it ran with the real production key still loaded via
load_dotenv() and made one real, billed-against-quota call before the fix).
Everything below is mocked at _client.post
with real httpx.Response objects (not hand-rolled Mocks), so raise_for_status()
and .json() behave exactly as they would against the real API.
"""
from unittest.mock import patch

import httpx
import pytest

from src.db.models import get_usage_summary
from src.integrations.shipping import ShippingNotConfiguredError, get_tracking_status


def _response(status_code: int, json_data: dict) -> httpx.Response:
    request = httpx.Request("POST", "https://api.ship24.com/public/v1/tracking/search")
    return httpx.Response(status_code, json=json_data, request=request)


def test_raises_not_configured_when_key_missing():
    with pytest.raises(ShippingNotConfiguredError):
        get_tracking_status("1Z999AA10123456784")


@pytest.fixture()
def with_ship24_key(monkeypatch):
    """SHIP24_API_KEY is a bound module-level name in shipping.py (imported
    from src.config at import time), so the DB_PATH pattern applies here too:
    patch the module attribute, not the env var."""
    import src.integrations.shipping as shipping

    monkeypatch.setattr(shipping, "SHIP24_API_KEY", "test-ship24-key")


def test_returns_status_milestone_from_first_tracking(with_ship24_key, db_path):
    payload = {"data": {"trackings": [{"shipment": {"statusMilestone": "transit", "statusCode": "in_transit"}}]}}
    with patch("src.integrations.shipping._client.post", return_value=_response(200, payload)):
        result = get_tracking_status("TRACK123")

    assert result == {"status_milestone": "transit", "status_code": "in_transit"}


def test_returns_none_milestone_when_no_trackings_found(with_ship24_key, db_path):
    """A tracking number no carrier has registered yet - not an error, a
    valid empty result (see scheduler._has_never_registered, which relies on
    exactly this shape to decide when to give up)."""
    payload = {"data": {"trackings": []}}
    with patch("src.integrations.shipping._client.post", return_value=_response(200, payload)):
        result = get_tracking_status("UNKNOWN-NUMBER")

    assert result == {"status_milestone": None, "status_code": None}


def test_logs_quota_usage_even_on_success(with_ship24_key, db_path):
    payload = {"data": {"trackings": [{"shipment": {"statusMilestone": "delivered"}}]}}
    with patch("src.integrations.shipping._client.post", return_value=_response(200, payload)):
        get_tracking_status("TRACK1")

    assert get_usage_summary()["ship24"]["today"]["calls"] == 1


def test_logs_quota_usage_even_when_the_api_call_fails(with_ship24_key, db_path):
    """The comment in the source is explicit about this: a call against the
    100/month quota happened whether or not it succeeded, so the usage log
    must reflect that even on a failing response."""
    with patch("src.integrations.shipping._client.post", return_value=_response(500, {})):
        with pytest.raises(httpx.HTTPStatusError):
            get_tracking_status("TRACK1")

    assert get_usage_summary()["ship24"]["today"]["calls"] == 1

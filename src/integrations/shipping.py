"""
Package tracking via Ship24's per-call Tracking API (the "Tracking API
(Per-call plans)" product - free tier, 100 calls/month) - not the
per-shipment/webhook product, which is a separate subscription this account
doesn't have.

The per-call API is stateless: POST /tracking/search returns tracking
results directly in the same call, with no persistent tracker to register
first (verified live against the real API - see the plan/commit history for
the confirmed response shape). Every call counts against the 100/month
quota, whether it's the first check of a tracking number or the hundredth,
so callers should throttle repeat checks (see webhook_handler's
_handle_package_status, which only re-checks a given package at most once
per hour).
"""
import httpx

from src.config import SHIP24_API_KEY

BASE_URL = "https://api.ship24.com/public/v1"

_client = httpx.Client(timeout=10.0)


class ShippingNotConfiguredError(Exception):
    """SHIP24_API_KEY is missing from .env."""


def get_tracking_status(tracking_number: str) -> dict:
    """
    Returns {status_milestone, status_code} for a tracking number - a single
    stateless call, no registration step. status_milestone is Ship24's own
    coarse category (e.g. "pending", "transit", "delivered") and is the
    simplest field for a one-line human summary.
    """
    if not SHIP24_API_KEY:
        raise ShippingNotConfiguredError()

    resp = _client.post(
        f"{BASE_URL}/tracking/search",
        headers={"Authorization": f"Bearer {SHIP24_API_KEY}"},
        json={"trackingNumber": tracking_number},
    )

    try:
        # Logged regardless of status code - a call against the 100/month
        # quota happened either way. Late import + non-fatal, same
        # convention as gemini.py's usage logging.
        from src.db.models import log_api_usage
        log_api_usage("ship24")
    except Exception as e:
        print(f"[shipping] usage logging failed (non-fatal): {e}")

    resp.raise_for_status()
    data = resp.json()

    trackings = data.get("data", {}).get("trackings", [])
    if not trackings:
        return {"status_milestone": None, "status_code": None}

    shipment = trackings[0].get("shipment", {})
    return {
        "status_milestone": shipment.get("statusMilestone"),
        "status_code": shipment.get("statusCode"),
    }

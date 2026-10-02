"""
Fetches a URL and extracts its main article text (stripped of nav/ads/
boilerplate), for the "saved links" feature - the point is a permanent copy
of the actual content, not just the URL, per PRD founding principle #1.

Fetching is done with our own httpx client (same browser-like headers as
markets.py) rather than letting trafilatura fetch on its own, so every
outbound HTTP call in this codebase shares one consistent timeout/header
convention.
"""
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx
import trafilatura

MAX_SNAPSHOT_CHARS = 20_000


class UnsafeURLError(Exception):
    """The URL (or, after following redirects, some URL along the way)
    resolves to a private/internal/reserved address."""


def _is_unsafe_address(host: str) -> bool:
    """
    True if `host` (already stripped of scheme/port) resolves to an address
    this process should not be allowed to reach on the caller's behalf -
    loopback, link-local, private/RFC1918, or otherwise reserved. This is an
    allowlisted user's saved-link URL, not an authenticated admin action, and
    the fetch runs from inside the home network - so the request could
    otherwise reach the Zabbix API (localhost:8081), the UniFi controller
    (192.168.1.1), or anything else on the LAN, and the bot would happily
    read the response back to the user over WhatsApp.

    Resolves DNS itself and checks the resolved IP(s), not just the literal
    hostname string - a hostname that LOOKS external can still resolve to an
    internal address (DNS rebinding), and a literal-IP check alone would miss
    that entirely.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True  # cannot resolve at all - treat as unsafe rather than let httpx try anyway
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return True
    return False


def _guard_request(request: httpx.Request) -> None:
    """
    httpx event hook, invoked for the initial request AND for every
    redirect hop httpx follows internally (follow_redirects=True on the
    client below) - checking only the URL the caller passed in would miss a
    URL that starts out external and 302s to an internal address, which is
    exactly the well-known redirect-based SSRF bypass this closes.
    """
    parts = urlsplit(str(request.url))
    if parts.scheme not in ("http", "https"):
        raise UnsafeURLError(f"unsupported scheme: {parts.scheme!r}")
    if not parts.hostname or _is_unsafe_address(parts.hostname):
        raise UnsafeURLError(f"unsafe host: {parts.hostname!r}")


_client = httpx.Client(
    timeout=10.0,
    follow_redirects=True,
    headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    },
    event_hooks={"request": [_guard_request]},
)


def fetch_and_extract(url: str) -> dict:
    """
    Returns {title, text, content_type, fetch_status}.

    fetch_status:
      - "success": real article text was extracted
      - "paywalled": the page loaded but almost no real content came through
        (best-effort heuristic - true paywall detection isn't reliable from
        the HTML alone, per PRD's own open-risk note on this)
      - "failed": the request itself failed (network error, non-2xx, no
        extractable content, or the URL was blocked as unsafe - the last one
        is deliberately indistinguishable from an ordinary failure in the
        return value, so this function never tells a caller "that was an
        internal address", only "that link couldn't be saved")

    On every outcome, the URL itself is preserved by the caller regardless -
    losing the link is worse than losing the content snapshot.
    """
    try:
        resp = _client.get(url)
        resp.raise_for_status()
    except (httpx.HTTPError, UnsafeURLError):
        return {"title": None, "text": None, "content_type": None, "fetch_status": "failed"}

    html = resp.text
    metadata = trafilatura.extract_metadata(html, default_url=url)
    title = metadata.title if metadata else None

    text = trafilatura.extract(html, include_comments=False, favor_precision=True)

    if not text:
        return {"title": title, "text": None, "content_type": "article", "fetch_status": "failed"}

    if len(text) < 500 and len(html) > 20_000:
        # A real page loaded (lots of HTML) but almost nothing extractable -
        # classic paywall/login-wall shape. Still keep whatever little text
        # trafilatura found, truncated like normal.
        return {
            "title": title,
            "text": text[:MAX_SNAPSHOT_CHARS],
            "content_type": "article",
            "fetch_status": "paywalled",
        }

    return {
        "title": title,
        "text": text[:MAX_SNAPSHOT_CHARS],
        "content_type": "article",
        "fetch_status": "success",
    }

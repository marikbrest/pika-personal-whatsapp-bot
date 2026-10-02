"""
Regression tests for the admin-panel auth bypass found and fixed on
2026-09-13 (commit 0305702, "Bind to loopback only - close LAN-wide admin
auth bypass").

Scope note: admin_handler._authorized() checks two HTTP headers (Host,
Cf-Access-Authenticated-User-Email), neither backed by a Cloudflare-signed
token. TestClient talks to the app in-process, with no network layer at all,
so it CANNOT exercise the actual vulnerability, which was that the app was
reachable directly on the LAN in the first place (0.0.0.0 binding) - that
half of the fix is infrastructure, verified manually against the live
server, not something a unit test can see.

What these tests DO cover, and why it still matters even with the network
path closed: the header-check logic is the only thing standing between "an
attacker on the LAN" and "an attacker anywhere that can reach this process"
if the loopback binding is ever loosened again by accident (a rebuilt start
script, someone debugging locally with --host 0.0.0.0, a future refactor).
A broken header check would turn that mistake back into the same
full-admin-takeover bug, silently. These tests exist to catch that class of
regression even though they cannot catch the network-level one.
"""
from .conftest import ADMIN_ALLOWED_EMAIL, ADMIN_HOST


def test_no_headers_at_all_is_rejected(client):
    resp = client.get("/admin/")
    assert resp.status_code == 403


def test_correct_host_but_no_access_email_header_is_rejected(client):
    """This is the exact shape of the confirmed exploit: only the Host header
    was forged, no Cf-Access-Authenticated-User-Email at all. Must be 403,
    not 200 - if this test ever passes with a 200, the header check
    (not just the network binding) has regressed."""
    resp = client.get("/admin/", headers={"Host": ADMIN_HOST})
    assert resp.status_code == 403


def test_access_email_header_alone_without_correct_host_is_rejected(client):
    """The public webhook host (assistant.your-domain.example) must not grant admin
    access even if a Cf-Access-Authenticated-User-Email header is somehow
    present - the two checks are meant to be independent, not
    either/or."""
    resp = client.get(
        "/admin/",
        headers={"Host": "assistant.your-domain.example", "Cf-Access-Authenticated-User-Email": ADMIN_ALLOWED_EMAIL},
    )
    assert resp.status_code == 403


def test_wrong_allowed_email_is_rejected(client):
    """Correct host, a Cf-Access-Authenticated-User-Email header IS present
    (so this simulates a real Cloudflare Access login - just the wrong
    person, e.g. Ronit's own Google account also being allowed by Access
    policy but not listed in ADMIN_ALLOWED_EMAIL)."""
    resp = client.get(
        "/admin/",
        headers={"Host": ADMIN_HOST, "Cf-Access-Authenticated-User-Email": "someone-else@example.com"},
    )
    assert resp.status_code == 403


def test_correct_host_and_allowed_email_is_accepted(client):
    """The one legitimate combination - documents what a real request coming
    through the Cloudflare Tunnel + Access, for the allowed account, actually
    looks like by the time it reaches this code."""
    resp = client.get(
        "/admin/",
        headers={"Host": ADMIN_HOST, "Cf-Access-Authenticated-User-Email": ADMIN_ALLOWED_EMAIL},
    )
    assert resp.status_code == 200
    assert "ניהול העוזר האישי" in resp.text


def test_access_email_check_is_case_insensitive_on_both_sides(client):
    """_authorized() lowercases both sides before comparing - Cloudflare Access
    and Google account emails are not guaranteed consistent casing."""
    resp = client.get(
        "/admin/",
        headers={"Host": ADMIN_HOST.upper(), "Cf-Access-Authenticated-User-Email": ADMIN_ALLOWED_EMAIL.upper()},
    )
    assert resp.status_code == 200


def test_restart_endpoint_also_requires_auth(client):
    """The destructive action (kills the running process), not just the read-only
    pages, must be behind the same check - a partial fix that only locked the
    dashboard's GET routes would still allow a DoS."""
    resp = client.post("/admin/restart", headers={"Host": "assistant.your-domain.example"})
    assert resp.status_code == 403

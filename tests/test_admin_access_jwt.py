"""
Admin dashboard identity: fail-closed without ADMIN_ALLOWED_EMAIL, and verification of the
signed Cloudflare Access JWT when CF_ACCESS_TEAM_DOMAIN/CF_ACCESS_AUD are configured (so a
forged Cf-Access-Authenticated-User-Email header is worthless).
"""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

import src.admin_handler as admin_handler

from .conftest import ADMIN_ALLOWED_EMAIL, ADMIN_HOST

TEAM = "myteam.cloudflareaccess.com"
AUD = "test-aud-tag"


@pytest.fixture
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def jwt_mode(monkeypatch, signing_key):
    class _FakeJwks:
        def get_signing_key_from_jwt(self, token):
            return type("K", (), {"key": signing_key.public_key()})()

    monkeypatch.setattr(admin_handler, "CF_ACCESS_TEAM_DOMAIN", TEAM)
    monkeypatch.setattr(admin_handler, "CF_ACCESS_AUD", AUD)
    monkeypatch.setattr(admin_handler, "_jwks_client", _FakeJwks())


def _token(key, email=ADMIN_ALLOWED_EMAIL, aud=AUD, iss=f"https://{TEAM}", exp_in=300):
    now = int(time.time())
    return jwt.encode({"email": email, "aud": aud, "iss": iss, "iat": now, "exp": now + exp_in}, key, algorithm="RS256")


def test_dashboard_is_off_without_an_allowed_email(client, monkeypatch):
    monkeypatch.setattr(admin_handler, "ADMIN_ALLOWED_EMAIL", "")
    resp = client.get("/admin/", headers={"Host": ADMIN_HOST, "Cf-Access-Authenticated-User-Email": "anyone@example.com"})
    assert resp.status_code == 403


def test_valid_jwt_is_accepted(client, jwt_mode, signing_key):
    resp = client.get("/admin/", headers={"Host": ADMIN_HOST, "Cf-Access-Jwt-Assertion": _token(signing_key)})
    assert resp.status_code == 200


def test_forged_email_header_is_ignored_in_jwt_mode(client, jwt_mode):
    resp = client.get("/admin/", headers={"Host": ADMIN_HOST, "Cf-Access-Authenticated-User-Email": ADMIN_ALLOWED_EMAIL})
    assert resp.status_code == 403


@pytest.mark.parametrize("kwargs", [
    {"aud": "some-other-app"},
    {"iss": "https://evil.cloudflareaccess.com"},
    {"exp_in": -10},
    {"email": "someone-else@example.com"},
])
def test_invalid_jwt_is_rejected(client, jwt_mode, signing_key, kwargs):
    resp = client.get("/admin/", headers={"Host": ADMIN_HOST, "Cf-Access-Jwt-Assertion": _token(signing_key, **kwargs)})
    assert resp.status_code == 403


def test_jwt_signed_by_another_key_is_rejected(client, jwt_mode):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    resp = client.get("/admin/", headers={"Host": ADMIN_HOST, "Cf-Access-Jwt-Assertion": _token(other)})
    assert resp.status_code == 403


def test_garbage_token_is_rejected(client, jwt_mode):
    resp = client.get("/admin/", headers={"Host": ADMIN_HOST, "Cf-Access-Jwt-Assertion": "not.a.jwt"})
    assert resp.status_code == 403

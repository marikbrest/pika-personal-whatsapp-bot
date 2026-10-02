"""
OAuth "state" is the only thing binding a Google consent callback back to a
specific WhatsApp user - there is no conventional web session here. It is
Fernet-encrypted (tamper-evident + self-expiring), which is the property
these tests exist to prove actually holds, not just assume from the
docstring.
"""
from cryptography.fernet import Fernet

from src.integrations import google_oauth
from src.integrations.google_oauth import build_auth_url, decode_state


def test_state_round_trips_the_user_id():
    url = build_auth_url(user_id=42)
    # state is a URL query parameter inside the Google auth_url
    state = dict(pair.split("=", 1) for pair in url.split("?", 1)[1].split("&"))["state"]
    from urllib.parse import unquote

    assert decode_state(unquote(state)) == 42


def test_tampered_state_is_rejected():
    url = build_auth_url(user_id=42)
    from urllib.parse import unquote

    state = unquote(dict(pair.split("=", 1) for pair in url.split("?", 1)[1].split("&"))["state"])
    tampered = state[:-4] + ("A" if state[-4] != "A" else "B") + state[-3:]
    assert decode_state(tampered) is None


def test_state_encrypted_with_a_different_key_is_rejected():
    """Simulates TOKEN_ENCRYPTION_KEY having been rotated between issuing the
    link and the user clicking it - must fail closed, not crash."""
    other_key = Fernet.generate_key()
    forged_state = Fernet(other_key).encrypt(b'{"user_id": 42}').decode()
    assert decode_state(forged_state) is None


def test_garbage_string_is_rejected_without_raising():
    assert decode_state("not-a-valid-fernet-token-at-all") is None


def test_expired_state_is_rejected(monkeypatch):
    """Uses a negative ttl rather than sleeping or freezing time. Fernet
    timestamps have 1-second resolution, so ttl=0 would be flaky - it only
    rejects once the wall clock has actually crossed a second boundary
    between encrypt and decrypt, which this fast, no-I/O test might not do.
    ttl=-1 makes 'current_time > timestamp + ttl' true unconditionally
    (current_time is always >= timestamp, since decrypt runs after encrypt),
    exercising the exact same rejection path deterministically."""
    url = build_auth_url(user_id=42)
    from urllib.parse import unquote

    state = unquote(dict(pair.split("=", 1) for pair in url.split("?", 1)[1].split("&"))["state"])

    monkeypatch.setattr(google_oauth, "_STATE_MAX_AGE_SECONDS", -1)
    assert decode_state(state) is None

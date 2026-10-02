"""
Regression tests for the SSRF fix in content_extractor.py (2026-09-13):
saved links can be any URL a user sends, fetched from inside the home
network, so an unguarded fetch could reach Zabbix/UniFi/anything else on the
LAN and read results back over WhatsApp.
"""
import httpx
import pytest

from src.integrations.content_extractor import UnsafeURLError, _guard_request, _is_unsafe_address, fetch_and_extract


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",  # loopback - would reach this very process (or Zabbix on the same box)
        "localhost",  # resolves to loopback
        "192.168.1.50",  # RFC1918 - a typical RFC1918 LAN address
        "192.168.1.1",  # the UniFi controller specifically
        "10.0.0.5",  # RFC1918, different block
        "169.254.169.254",  # link-local, the canonical cloud-metadata SSRF target
        "0.0.0.0",  # unspecified
    ],
)
def test_is_unsafe_address_blocks_internal_and_reserved_ranges(host):
    assert _is_unsafe_address(host) is True


def test_is_unsafe_address_allows_a_public_address():
    # A literal IP, not a hostname - getaddrinfo resolves it locally with no
    # real DNS/network round-trip, so this stays a hermetic unit test.
    assert _is_unsafe_address("8.8.8.8") is False


def test_is_unsafe_address_treats_unresolvable_host_as_unsafe():
    assert _is_unsafe_address("this-domain-does-not-exist-example-invalid.test") is True


def test_guard_request_rejects_non_http_scheme():
    request = httpx.Request("GET", "file:///etc/passwd")
    with pytest.raises(UnsafeURLError):
        _guard_request(request)


def test_guard_request_rejects_internal_ip():
    request = httpx.Request("GET", "http://127.0.0.1:8081/api_jsonrpc.php")
    with pytest.raises(UnsafeURLError):
        _guard_request(request)


def test_guard_request_allows_a_normal_external_https_url():
    request = httpx.Request("GET", "https://8.8.8.8/some/article")
    _guard_request(request)  # must not raise


def test_fetch_and_extract_blocks_internal_url_end_to_end_without_connecting():
    """The guard must fire before any actual socket connection is attempted -
    this passes even with nothing listening on 127.0.0.1:9999 in the test
    environment, because a real connection is never attempted at all."""
    result = fetch_and_extract("http://127.0.0.1:9999/whatever")
    assert result["fetch_status"] == "failed"
    # Deliberately no distinct error surfaced to the caller - see the
    # function's own docstring: "blocked as unsafe" must look identical to
    # any other network failure from the outside.
    assert result == {"title": None, "text": None, "content_type": None, "fetch_status": "failed"}


def test_fetch_and_extract_blocks_file_scheme():
    result = fetch_and_extract("file:///etc/passwd")
    assert result["fetch_status"] == "failed"

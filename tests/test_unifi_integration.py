"""
src.integrations.unifi - UNIFI_API_KEY is forced blank in conftest.py (see
the incident note there), so get_network_status() calling the real,
unmocked _call() exercises the genuine "not configured" path. Everything else
patches _call directly.
"""
from unittest.mock import patch

import pytest

from src.integrations import unifi
from src.integrations.unifi import UnifiNotConfiguredError, format_status_line, get_network_status


@pytest.fixture(autouse=True)
def _reset_site_cache():
    """_get_site_id() caches module-globally across calls - without
    resetting it, whichever test runs first would leak its fake site id into
    every later test in this file."""
    unifi._site_id_cache = None
    yield
    unifi._site_id_cache = None


def test_raises_not_configured_when_key_missing():
    with pytest.raises(UnifiNotConfiguredError):
        get_network_status()


def _fake_call(sites=None, clients_total=0, devices=None):
    sites = sites if sites is not None else [{"id": "site-1"}]
    devices = devices if devices is not None else []

    def _call(path):
        if path == "/sites":
            return {"data": sites}
        if path.startswith("/sites/") and "/clients" in path:
            return {"totalCount": clients_total}
        if path.startswith("/sites/") and "/devices" in path:
            return {"data": devices}
        raise AssertionError(f"unexpected path: {path}")

    return _call


def test_internet_up_when_dream_machine_is_online():
    devices = [{"model": "UniFi Dream Machine Pro", "state": "ONLINE"}]
    with patch("src.integrations.unifi._call", side_effect=_fake_call(clients_total=7, devices=devices)):
        status = get_network_status()
    assert status == {"client_count": 7, "internet_up": True}


def test_internet_down_when_dream_machine_is_offline():
    devices = [{"model": "UniFi Dream Machine Pro", "state": "OFFLINE"}]
    with patch("src.integrations.unifi._call", side_effect=_fake_call(devices=devices)):
        status = get_network_status()
    assert status["internet_up"] is False


def test_internet_down_when_no_dream_machine_device_present():
    """A device list that never even contains the gateway - e.g. it's
    offline enough to not report at all - must not be mistaken for 'up'."""
    devices = [{"model": "UniFi Switch 8", "state": "ONLINE"}]
    with patch("src.integrations.unifi._call", side_effect=_fake_call(devices=devices)):
        status = get_network_status()
    assert status["internet_up"] is False


def test_format_status_line_reports_up_and_down():
    up_text = format_status_line({"client_count": 12, "internet_up": True})
    assert "12" in up_text and "תקין" in up_text

    down_text = format_status_line({"client_count": 0, "internet_up": False})
    assert "לא זמין" in down_text

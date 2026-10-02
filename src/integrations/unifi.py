"""
Read-only queries against the UniFi Network Integration API - client count
and whether the gateway is reachable, for the same admin-only home-infra
status query that already reports Zabbix problems.

Uses a dedicated "whatsapp-bot" API key (not a key shared with other tools) - same least-privilege reasoning as the
dedicated Zabbix API token: if this key leaks, its blast radius is isolated
and audit trails show which consumer made which call.

Verified empirically against the live controller (v1 Integration API,
firmware as of 2026-09): there is no dedicated WAN/internet sub-object at
this API level - `problem.get`-style guessing at a field that doesn't exist
was exactly the mistake made and fixed for Zabbix earlier, so here the
gateway's own top-level `state` field is used as the reachability signal
instead, the same kind of proxy already relied on for Zabbix's ICMP checks.
"""
import httpx

from src.config import UNIFI_API_KEY, UNIFI_HOST

_client = httpx.Client(timeout=10.0, verify=False)  # self-signed cert, LAN-only endpoint

_site_id_cache: str | None = None


class UnifiNotConfiguredError(Exception):
    """UNIFI_HOST / UNIFI_API_KEY are missing from .env."""


def _call(path: str) -> dict:
    if not UNIFI_HOST or not UNIFI_API_KEY:
        raise UnifiNotConfiguredError()

    resp = _client.get(
        f"{UNIFI_HOST}/proxy/network/integration/v1{path}",
        headers={"X-API-KEY": UNIFI_API_KEY},
    )
    resp.raise_for_status()
    return resp.json()


def _get_site_id() -> str:
    """Caches the (single, personal-scale) site id - discovered rather than
    hardcoded, so a site rename/recreate doesn't silently break this."""
    global _site_id_cache
    if _site_id_cache is None:
        sites = _call("/sites")["data"]
        _site_id_cache = sites[0]["id"]
    return _site_id_cache


def get_network_status() -> dict:
    """Returns {client_count, internet_up}."""
    site_id = _get_site_id()

    clients = _call(f"/sites/{site_id}/clients?limit=1")
    client_count = clients["totalCount"]

    devices = _call(f"/sites/{site_id}/devices?limit=50")["data"]
    gateway = next((d for d in devices if "Dream Machine" in d.get("model", "")), None)
    internet_up = gateway is not None and gateway.get("state") == "ONLINE"

    return {"client_count": client_count, "internet_up": internet_up}


def format_status_line(status: dict) -> str:
    """One line summarizing UniFi status, meant to be appended to the Zabbix
    problems reply rather than sent standalone."""
    internet_text = "תקין ✅" if status["internet_up"] else "לא זמין ❌"
    return f"🌐 UniFi: {status['client_count']} מכשירים מחוברים, אינטרנט {internet_text}"

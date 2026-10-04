"""
Read-only query against the Firewalla MSP API - box online state and active
alarm count, for the same admin-only home-infra status query that already
reports Zabbix problems and UniFi network status.

Uses a dedicated "whatsapp-bot" MSP personal access token, same
least-privilege reasoning as the Zabbix/UniFi tokens: if this key leaks,
its blast radius is isolated and MSP's own audit trail shows which
consumer made which call. Unlike those two, the Firewalla MSP API has no
per-token scoping (a personal access token carries full account access),
so this is a case where least-privilege means "dedicated token" only, not
"dedicated + restricted".
"""
import httpx

from src.config import FIREWALLA_API_TOKEN, FIREWALLA_MSP_DOMAIN

_client = httpx.Client(timeout=10.0)


class FirewallaNotConfiguredError(Exception):
    """FIREWALLA_MSP_DOMAIN / FIREWALLA_API_TOKEN are missing from .env."""


def _call(path: str, params: dict | None = None) -> dict | list:
    if not FIREWALLA_MSP_DOMAIN or not FIREWALLA_API_TOKEN:
        raise FirewallaNotConfiguredError()

    resp = _client.get(
        f"https://{FIREWALLA_MSP_DOMAIN}{path}",
        headers={"Authorization": f"Token {FIREWALLA_API_TOKEN}"},
        params=params or {},
    )
    resp.raise_for_status()
    return resp.json()


def get_box_status() -> dict:
    """Returns {online, name, active_alarm_count} for the (single, personal-
    scale) box. active_alarm_count comes from a separate /v2/alarms query
    rather than the box's own alarmCount field, which is a lifetime total
    and not "currently active"."""
    boxes = _call("/v2/boxes")
    box = boxes[0]

    alarms = _call("/v2/alarms", params={"query": "status:1", "limit": 1})
    active_alarm_count = alarms["count"]

    return {
        "online": box["online"],
        "name": box["name"],
        "active_alarm_count": active_alarm_count,
    }


from src.i18n import t


def format_status_line(status: dict) -> str:
    """One line summarizing Firewalla status, meant to be appended to the
    Zabbix problems reply rather than sent standalone."""
    online_text = t("firewalla.online") if status["online"] else t("firewalla.offline")
    alarms_text = (
        t("firewalla.alarms", count=status["active_alarm_count"]) if status["active_alarm_count"] else t("firewalla.no_alarms")
    )
    return t("firewalla.status_line", name=status["name"], online=online_text, alarms=alarms_text)

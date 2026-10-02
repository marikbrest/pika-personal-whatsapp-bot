"""
Read-only queries against the Zabbix API for the home-lab monitoring stack
(you need your own Zabbix server; this bot only reads from its API).

Deliberately uses a dedicated, restricted Zabbix API token (a read-only user/
role created just for this bot) rather than the Admin account: if this token
ever leaks, it can only ever see monitoring data, never change a trigger,
host, or anything else in Zabbix.

The Zabbix server runs in Docker on the same Windows machine as this bot, so
the API is reached over localhost - no need to go through the public
zabbix.your-domain.example hostname or Cloudflare Access for this server-to-server call.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from src.config import DEFAULT_TIMEZONE, ZABBIX_API_TOKEN, ZABBIX_API_URL

_client = httpx.Client(timeout=10.0)

_SEVERITY_EMOJI = {
    "0": "⚪", "1": "🔵", "2": "🟡", "3": "🟠", "4": "🔴", "5": "🟣",
}


class ZabbixNotConfiguredError(Exception):
    """ZABBIX_API_URL / ZABBIX_API_TOKEN are missing from .env."""


def _call(method: str, params: dict) -> dict | list:
    if not ZABBIX_API_URL or not ZABBIX_API_TOKEN:
        raise ZabbixNotConfiguredError()

    resp = _client.post(
        ZABBIX_API_URL,
        json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
        headers={
            "Content-Type": "application/json-rpc",
            "Authorization": f"Bearer {ZABBIX_API_TOKEN}",
        },
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"Zabbix API error: {data['error']}")
    return data["result"]


def get_active_problems() -> list[dict]:
    """
    Returns every currently unresolved problem across all monitored hosts,
    most severe first, excluding problems tagged scope=notice (the ~100+
    auto-discovered "service not running" noise - ASUS/Bitdefender/Google
    Updater/etc - already filtered out of the Telegram alert pipeline for the
    same reason: they're expected/on-demand services, not real problems).
    Each item: {host, description, severity, since}.

    problem.get in this Zabbix version doesn't support selectHosts directly
    (rejected as an unexpected parameter), so host names are resolved with a
    second call to trigger.get, which does support it. That same call also
    tells us which triggers are disabled, which is used to drop stale problems
    (see below).
    """
    result = _call(
        "problem.get",
        {
            "output": ["name", "severity", "clock", "objectid"],
            "selectTags": "extend",
        },
    )

    result = [
        p for p in result
        if not any(t["tag"] == "scope" and t["value"] == "notice" for t in p.get("tags") or [])
    ]
    if not result:
        return []

    # problem.get only supports sorting by eventid server-side, so severity
    # order (most severe first) is applied here instead.
    result.sort(key=lambda p: int(p["severity"]), reverse=True)

    trigger_ids = list({p["objectid"] for p in result})
    triggers = _call(
        "trigger.get",
        {"triggerids": trigger_ids, "output": ["triggerid", "status"], "selectHosts": ["host"]},
    )
    host_by_trigger = {
        t["triggerid"]: (t["hosts"][0]["host"] if t.get("hosts") else "?") for t in triggers
    }
    # Disabling a trigger does not necessarily clear problems it had already
    # raised - they can sit in the problem table indefinitely. Reporting one is
    # worse than useless: it is an alert nobody intends to act on, about a check
    # that was deliberately switched off, and it trains you to ignore the rest.
    # Observed with "Linux: Zabbix agent is not available" on the Zabbix server
    # host, which kept being reported for ~2 weeks after the trigger was turned
    # off. status "1" means disabled.
    disabled_triggers = {t["triggerid"] for t in triggers if t.get("status") == "1"}

    problems = []
    for p in result:
        if p["objectid"] in disabled_triggers:
            continue
        problems.append(
            {
                "host": host_by_trigger.get(p["objectid"], "?"),
                "description": p["name"],
                "severity": p["severity"],
                "since": datetime.fromtimestamp(int(p["clock"]), tz=timezone.utc),
            }
        )
    return problems


def format_problems_for_reply(problems: list[dict], timezone_name: str) -> str:
    """Formats the get_active_problems result as readable Hebrew text. Never
    goes through Gemini - this is a fact, not a guess."""
    if not problems:
        return "✅ הכל תקין, אין בעיות פתוחות בזאביקס כרגע."

    tz = ZoneInfo(timezone_name or DEFAULT_TIMEZONE)
    lines = ["📡 מצב הניטור (Zabbix):"]
    for p in problems:
        emoji = _SEVERITY_EMOJI.get(p["severity"], "⚪")
        since_local = p["since"].astimezone(tz)
        lines.append(f"{emoji} {p['host']}: {p['description']} (מ-{since_local.strftime('%d/%m %H:%M')})")
    return "\n".join(lines)

"""
Parses the [timing] lines from the log file for the dashboard statistics.

The data source is the log rather than the DB because response times are
measured at runtime and only printed to the log (see _log_timing in
webhook_handler). That is good enough for a personal dashboard; if long-term
history is ever needed, the measurements should be persisted to a table.

Format of the parsed line:
[timing] user=1 type=text intent=chat total=1.87s (idempotency_check=0.01s gemini=1.71s ...)
"""
import re
from collections import defaultdict
from pathlib import Path

_TIMING_RE = re.compile(
    r"\[timing\] user=(?P<user>\S+) type=(?P<type>\S+) intent=(?P<intent>\S+) total=(?P<total>[\d.]+)s"
)

# How many trailing lines to read. The log can grow large and there is no
# point scanning all of it on every page load.
_MAX_LINES = 5000


def parse_timing_stats(log_path: Path) -> dict:
    """
    Returns aggregate statistics from the log:
    {
      "total_requests": int,
      "by_intent": {intent: {"count": int, "avg_total": float}},
      "by_type": {"text": int, "audio": int},
      "avg_total": float,
      "slowest": float,
    }
    Returns an empty structure (rather than raising) if the file is missing or
    unreadable - the dashboard must still load when no log exists yet.
    """
    empty = {"total_requests": 0, "by_intent": {}, "by_type": {}, "avg_total": 0.0, "slowest": 0.0}

    try:
        if not log_path.exists():
            return empty
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-_MAX_LINES:]
    except OSError:
        return empty

    totals_by_intent: dict[str, list[float]] = defaultdict(list)
    count_by_type: dict[str, int] = defaultdict(int)
    all_totals: list[float] = []

    for line in lines:
        m = _TIMING_RE.search(line)
        if not m:
            continue
        try:
            total = float(m.group("total"))
        except ValueError:
            continue
        totals_by_intent[m.group("intent")].append(total)
        count_by_type[m.group("type")] += 1
        all_totals.append(total)

    if not all_totals:
        return empty

    by_intent = {
        intent: {"count": len(vals), "avg_total": sum(vals) / len(vals)}
        for intent, vals in sorted(totals_by_intent.items(), key=lambda kv: -len(kv[1]))
    }

    return {
        "total_requests": len(all_totals),
        "by_intent": by_intent,
        "by_type": dict(count_by_type),
        "avg_total": sum(all_totals) / len(all_totals),
        "slowest": max(all_totals),
    }

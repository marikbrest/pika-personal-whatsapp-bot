# -*- coding: utf-8 -*-
"""
Stage B report: how often does the new tools pipeline agree with the old
classifier, on real live traffic?

Read-only - queries intent_shadow_log (populated in the background by
src.tools.shadow, never acted on). Splits into two groups deliberately:

- "migrated" rows: old_intent is one of weather/market/task_manage - the 3
  tools that actually exist in the new pipeline so far. Agreement here is
  the real signal this migration cares about right now.
- everything else: old_intent was something the new pipeline has no tool
  for yet (calendar, email, ...), so it correctly falls back to chat/unclear
  almost always. That is NOT disagreement in any meaningful sense - it is
  exactly what should happen with only 3 of ~20 tools migrated, comparing 20
  options against 5. Shown separately so it never gets mixed into the one
  number that actually matters for a cutover decision.

Run: venv\\Scripts\\python.exe scripts\\report_shadow_agreement.py
"""
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from src.db.models import get_connection

# old_intent -> the new tool name that should represent full agreement.
MIGRATED_INTENT_TO_TOOL = {
    "weather": "get_weather",
    "market": "get_market_quote",
    "task_manage": "manage_tasks",
}

conn = get_connection()
rows = conn.execute(
    "SELECT whatsapp_message_id, raw_content, old_intent, new_tool, new_args, error, created_at "
    "FROM intent_shadow_log ORDER BY id"
).fetchall()
conn.close()

print(f"total shadow comparisons logged: {len(rows)}")
if not rows:
    print("\nNothing yet - shadow logging only started with this deploy; it builds up as real messages arrive.")
    sys.exit(0)

migrated = [r for r in rows if r["old_intent"] in MIGRATED_INTENT_TO_TOOL]
other = [r for r in rows if r["old_intent"] not in MIGRATED_INTENT_TO_TOOL]

print(f"\n=== migrated tools (weather/market/task_manage) - {len(migrated)} comparisons ===")
if not migrated:
    print("  none yet")
else:
    agree = sum(1 for r in migrated if r["new_tool"] == MIGRATED_INTENT_TO_TOOL[r["old_intent"]])
    errors = sum(1 for r in migrated if r["error"])
    print(f"  agree: {agree}/{len(migrated)} ({100*agree/len(migrated):.0f}%)")
    print(f"  new-pipeline errors: {errors}/{len(migrated)}")
    print("\n  disagreements:")
    for r in migrated:
        expected = MIGRATED_INTENT_TO_TOOL[r["old_intent"]]
        if r["new_tool"] == expected:
            continue
        print(f"    [{r['created_at']}] {r['raw_content']!r}")
        print(f"      old={r['old_intent']}  new={r['new_tool']!r}  args={r['new_args']}  error={r['error']}")

print(f"\n=== not-yet-migrated intents - {len(other)} comparisons (informational only) ===")
if other:
    by_new_tool: dict[str, int] = {}
    for r in other:
        by_new_tool[r["new_tool"] or "(error)"] = by_new_tool.get(r["new_tool"] or "(error)", 0) + 1
    for tool, count in sorted(by_new_tool.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>4}  fell to {tool}")
    unexpected = [r for r in other if r["new_tool"] not in ("chat", "unclear", None)]
    if unexpected:
        print(f"\n  ⚠️ {len(unexpected)} case(s) where a non-migrated intent matched an existing "
              f"tool anyway - worth a look (the new pipeline may be over-eager):")
        for r in unexpected[:10]:
            print(f"    [{r['created_at']}] {r['raw_content']!r}  old={r['old_intent']} new={r['new_tool']}")

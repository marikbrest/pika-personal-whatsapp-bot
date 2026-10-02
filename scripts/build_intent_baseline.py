# -*- coding: utf-8 -*-
"""
Step 0 of the function-calling migration: builds a regression baseline by
running the CURRENT classifier (src.intent_parser.parse_message) over every
historical incoming text message that has no parsed_intent recorded yet -
i.e. everything sent before save_incoming_message started logging it.

This is deliberately a snapshot of "what today's hand-rolled JSON classifier
says", not a claim about what's actually correct - some of these are
certainly misclassifications; that's exactly the point. Once the
function-calling version exists, running it over the same messages and
diffing against this baseline is what tells us whether the migration
changed behavior, without having to manually re-judge every message by
hand. Real disagreements still need a human to say which side was right;
this just makes "did anything change" a five-second diff instead of a
guess.

Deliberately NOT reconstructing each message's original conversation
history/contacts/pending-draft context - parse_message() is called with
just the raw text, no history. Two reasons: reconstructing "what the
classifier would have seen for message N, threaded through every reminder/
draft/contact state change up to that point" is a much bigger undertaking
for uncertain benefit (only follow-up-style messages - "לא, תעשה את זה
ב-10" - depend on history at all), and a no-context classification is more
useful as a baseline anyway: it is exactly reproducible on demand, unlike
the real conversation state at the time, which cannot be reconstructed
after the fact. This means a handful of context-dependent messages will
classify as "unclear" here where the live bot actually got them right (or
wrong differently) at the time - a known, acceptable gap in this baseline,
not a bug.

Run: venv\\Scripts\\python.exe scripts\\build_intent_baseline.py [--apply]
"""
import io
import os
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

from src.db.models import get_connection
from src.intent_parser import parse_message

DRY_RUN = "--apply" not in sys.argv

conn = get_connection()
rows = conn.execute(
    "SELECT id, raw_content FROM messages "
    "WHERE direction = 'incoming' AND message_type = 'text' "
    "AND parsed_intent IS NULL AND raw_content IS NOT NULL AND TRIM(raw_content) != '' "
    "ORDER BY id"
).fetchall()
conn.close()

print(f"text messages with no parsed_intent yet: {len(rows)}")
if DRY_RUN:
    print("\nDRY RUN - nothing written. Re-run with --apply to perform the backfill.")
    for r in rows[:5]:
        print(f"  sample id={r['id']} {r['raw_content'][:60]!r}")
    sys.exit(0)

done = failed = 0
by_intent: dict[str, int] = {}
start = time.time()
for i, r in enumerate(rows, 1):
    try:
        result = parse_message(r["raw_content"])
        intent = result["intent"]
    except Exception as e:
        failed += 1
        print(f"  id={r['id']} classification failed: {type(e).__name__}: {str(e)[:90]}")
        time.sleep(1)
        continue

    conn = get_connection()
    try:
        conn.execute("UPDATE messages SET parsed_intent = ? WHERE id = ?", (intent, r["id"]))
        conn.commit()
    finally:
        conn.close()
    done += 1
    by_intent[intent] = by_intent.get(intent, 0) + 1
    if i % 50 == 0:
        print(f"  {i}/{len(rows)}  ({time.time() - start:.0f}s elapsed)")

print(f"\nbaseline built: classified={done} failed={failed} in {time.time() - start:.0f}s")
print("\nintent distribution:")
for intent, count in sorted(by_intent.items(), key=lambda kv: -kv[1]):
    print(f"  {count:>4}  {intent}")

# -*- coding: utf-8 -*-
"""
One-time backfill of message embeddings.

Semantic search only reads rows where embedding IS NOT NULL, so every message
from before the feature shipped (2026-09-05) was invisible to it. This walks the
gap and embeds them, oldest first, committing per row so an interruption just
means resuming rather than starting over.
"""
import io, sys, os, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
from dotenv import load_dotenv; load_dotenv(os.path.join(ROOT, ".env"))
from src.db.models import get_connection
from src.integrations.embeddings import embed_text

DRY_RUN = "--apply" not in sys.argv

conn = get_connection()
rows = conn.execute(
    "SELECT id, user_id, raw_content FROM messages "
    "WHERE embedding IS NULL AND raw_content IS NOT NULL AND TRIM(raw_content) != '' "
    "ORDER BY id"
).fetchall()
conn.close()

# Placeholders carry no meaning worth searching and would only add noise to
# results; skip rather than spend a call on them.
SKIP = {"[voice message]", "[image]", "[document]"}
todo = [r for r in rows if r["raw_content"].strip() not in SKIP]
skipped = len(rows) - len(todo)

print(f"messages needing an embedding: {len(rows)}  (skipping {skipped} placeholders)")
print(f"to embed: {len(todo)}")
if DRY_RUN:
    print("\nDRY RUN - nothing written. Re-run with --apply to perform the backfill.")
    for r in todo[:3]:
        print(f"  sample id={r['id']} user={r['user_id']} {r['raw_content'][:50]!r}")
    sys.exit(0)

done = failed = 0
start = time.time()
for i, r in enumerate(todo, 1):
    try:
        embedding = embed_text(r["raw_content"])
    except Exception as e:
        failed += 1
        print(f"  id={r['id']} failed: {type(e).__name__}: {str(e)[:90]}")
        time.sleep(1)  # back off a little in case it is a rate limit
        continue
    conn = get_connection()
    try:
        conn.execute("UPDATE messages SET embedding = ? WHERE id = ?", (embedding, r["id"]))
        conn.commit()
    finally:
        conn.close()
    done += 1
    if i % 50 == 0:
        print(f"  {i}/{len(todo)}  ({time.time() - start:.0f}s elapsed)")

print(f"\nbackfilled={done} failed={failed} in {time.time() - start:.0f}s")
conn = get_connection()
left = conn.execute("SELECT COUNT(*) FROM messages WHERE embedding IS NULL").fetchone()[0]
total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
conn.close()
print(f"messages with embeddings: {total - left}/{total}")

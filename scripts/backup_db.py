"""
Consistent, cross-platform backup of the SQLite database.

    python scripts/backup_db.py                 # -> backups/assistant_<timestamp>.db
    python scripts/backup_db.py --keep 30       # keep the newest 30 (default 14)
    python scripts/backup_db.py --out /mnt/nas  # write somewhere else

Uses SQLite's online-backup API, so it is safe while the bot is running (a plain file
copy can catch the database mid-write). Reads DB_PATH from .env / the environment, same
as the bot; BACKUP_DIR overrides the output folder.

Optional offsite copy: set BACKUP_RCLONE_REMOTE (e.g. "myremote:pika-backups") and have
`rclone` on PATH - the new backup is uploaded and offsite copies older than 60 days are
pruned. A failed upload is reported but never fails the (already successful) local backup.

Docker:  docker compose exec bot python scripts/backup_db.py --out /data/backups
         (then copy /data/backups out of the pika-data volume)
Schedule it with cron / systemd timer / Windows Task Scheduler
(scripts/register_backup_task.ps1 does the latter).
"""
import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))
except ImportError:  # python-dotenv is a hard dependency of the bot, but keep this script standalone
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Back up the Pika SQLite database.")
    parser.add_argument("--out", default=os.environ.get("BACKUP_DIR") or os.path.join(ROOT, "backups"))
    parser.add_argument("--keep", type=int, default=14, help="how many local backups to keep (default 14)")
    args = parser.parse_args()

    db_path = os.environ.get("DB_PATH") or os.path.join(ROOT, "data", "assistant.db")
    if not os.path.exists(db_path):
        print(f"BACKUP FAILED: database not found at {db_path}", file=sys.stderr)
        return 1

    os.makedirs(args.out, exist_ok=True)
    target = os.path.join(args.out, f"assistant_{datetime.now():%Y-%m-%d_%H%M%S}.db")
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    except sqlite3.Error as e:
        print(f"BACKUP FAILED: {e}", file=sys.stderr)
        dst.close()
        src.close()
        if os.path.exists(target):
            os.remove(target)
        return 1
    dst.close()
    src.close()
    print(f"Backup created: {target}")

    old = sorted(f for f in os.listdir(args.out) if f.startswith("assistant_") and f.endswith(".db"))[: -max(args.keep, 1)]
    for name in old:
        os.remove(os.path.join(args.out, name))
        print(f"Deleted old backup: {name}")

    remote = os.environ.get("BACKUP_RCLONE_REMOTE")
    if remote:
        rclone = shutil.which("rclone")
        if not rclone:
            print("rclone not found on PATH - offsite copy SKIPPED")
        else:
            up = subprocess.run([rclone, "copy", target, remote, "--no-traverse", "--log-level", "ERROR"])
            if up.returncode != 0:
                print("OFFSITE UPLOAD FAILED (local backup is intact)", file=sys.stderr)
            else:
                print(f"Offsite copy uploaded to {remote}")
                subprocess.run([rclone, "delete", remote, "--min-age", "60d", "--log-level", "ERROR"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

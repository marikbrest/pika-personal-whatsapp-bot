"""
Fully remove one user and everything stored about them.

    python scripts/delete_user.py 972501234567 --dry-run     # show what would be deleted, change nothing
    python scripts/delete_user.py 972501234567               # asks you to retype the number, then deletes
    python scripts/delete_user.py 972501234567 --yes         # no prompt (scripts)

    --contacts       also remove this number from OTHER users' contact books (and the reminders addressed
                     to it) - for someone who asked to stop receiving reminders but was never a user
    --keep-google    do not revoke the stored Google grant at Google
    --force          allow deleting the last active admin

What it does: revokes the Google grant at Google, then deletes the user's messages (and embeddings),
contacts, reminders, facts, tasks, links, drafts, watches, packages, kids' schedules, proactive settings
and logs, and the user row, in one transaction. The admin audit log is kept (it records what admins did)
but no longer points at the user.

It does NOT touch backups: any backup made before today still contains this user's data until it is deleted
or rotated out (see scripts/backup_db.py --keep). Stop the bot or run it while it is idle if you can.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from src.db.models import get_connection, get_user_by_whatsapp_number, log_admin_action  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Fully remove a user and their data.")
    ap.add_argument("number", help="the user's WhatsApp number, digits only, e.g. 972501234567")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    ap.add_argument("--contacts", action="store_true")
    ap.add_argument("--keep-google", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    if not args.number.isdigit():
        print("The number must be digits only, in international format, without '+'.")
        return 1

    from src import user_deletion

    user = get_user_by_whatsapp_number(args.number)
    if user is None:
        print(f"No user with number {args.number}.")
        return 1

    conn = get_connection()
    try:
        other_admins = conn.execute(
            "SELECT COUNT(*) FROM users WHERE is_admin = 1 AND is_active = 1 AND id != ?", (user["id"],)
        ).fetchone()[0]
    finally:
        conn.close()
    if user["is_admin"] and not other_admins and not args.force:
        print("This is the last active admin; deleting them leaves nobody to manage the bot. Use --force to do it anyway.")
        return 1

    counts = user_deletion.describe(user["id"], args.contacts)
    print(f"User: {user['display_name']} ({args.number})")
    print("Would delete:" if args.dry_run else "Will delete:")
    for table, n in sorted(counts.items()):
        print(f"  {table}: {n}")
    if args.dry_run:
        print("(dry run - nothing was changed)")
        return 0

    if not args.yes:
        typed = input(f"Type the number {args.number} to confirm: ").strip()
        if typed != args.number:
            print("Not confirmed - nothing was changed.")
            return 1

    if not args.keep_google:
        from src.integrations.google_oauth import revoke_google_tokens

        result = revoke_google_tokens(user["id"])
        print(f"Google grant: {result}")
        if result == "failed":
            print("  Could not revoke it at Google. The user can do it at https://myaccount.google.com/permissions")

    deleted = user_deletion.delete_user(user["id"], args.contacts)
    log_admin_action("cli:delete_user", "delete_user", None, f"user_id={user['id']}; rows={sum(deleted.values())}")
    print(f"Deleted {sum(deleted.values())} rows. Backups made earlier still contain this user's data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Full removal of one user and everything stored about them (used by scripts/delete_user.py).

Which tables to clean is discovered from the schema (every table with a foreign key to users) rather than
hard-coded, so a table added later is covered automatically - and tests/test_user_deletion.py fails if a
new table holds data this module cannot delete.
"""
import sqlite3

from src.db.models import get_connection

# Tables that must be cleaned in a special way instead of the generic "delete rows pointing at this user":
#   contacts        - deleted last, after the reminders that reference them
#   admin_audit_log - kept (it documents what admins did), only the pointer to the user is removed
_SPECIAL = {"contacts", "admin_audit_log"}


def _tables_referencing(conn: sqlite3.Connection, parent: str) -> list[tuple[str, str]]:
    """[(table, column)] for every foreign key pointing at `parent`."""
    out = []
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for t in tables:
        for fk in conn.execute(f"PRAGMA foreign_key_list({t})").fetchall():
            if fk[2] == parent:
                out.append((t, fk[3]))
    return out


def _count(conn, table: str, where: str, args: tuple) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", args).fetchone()[0]


def _plan(conn: sqlite3.Connection, user_id: int, remove_from_other_contacts: bool) -> list[tuple[str, str, str, tuple]]:
    """Ordered steps as (label, kind, sql_where_or_sql, args). kind is 'delete' (DELETE FROM label WHERE ...),
    'null' (UPDATE label SET col=NULL WHERE ...) - label carries 'table.column' for 'null'."""
    user = conn.execute("SELECT whatsapp_number FROM users WHERE id = ?", (user_id,)).fetchone()
    number = user[0]
    steps: list[tuple[str, str, str, tuple]] = []

    # a shadow-log row has no user column, only the WhatsApp message id - find it through the user's messages
    steps.append((
        "intent_shadow_log", "delete",
        "whatsapp_message_id IN (SELECT whatsapp_message_id FROM messages WHERE user_id = ? AND whatsapp_message_id IS NOT NULL)",
        (user_id,),
    ))

    if remove_from_other_contacts:
        # other people's reminders addressed to this number go first (they reference those contact rows)
        sub = "SELECT id FROM contacts WHERE whatsapp_number = ? AND owner_user_id != ?"
        for t, col in _tables_referencing(conn, "contacts"):
            steps.append((t, "delete", f"{col} IN ({sub})", (number, user_id)))
        steps.append(("contacts", "delete", "whatsapp_number = ? AND owner_user_id != ?", (number, user_id)))

    for t, col in _tables_referencing(conn, "users"):
        if t in _SPECIAL:
            continue
        steps.append((t, "delete", f"{col} = ?", (user_id,)))
    # reminders / persistent_reminders also point at contacts; they are deleted here, before contacts below

    steps.append(("contacts", "delete", "owner_user_id = ?", (user_id,)))
    steps.append(("admin_audit_log.target_user_id", "null", "target_user_id = ?", (user_id,)))
    steps.append(("users", "delete", "id = ?", (user_id,)))
    return steps


def describe(user_id: int, remove_from_other_contacts: bool = False) -> dict[str, int]:
    """Row counts that deleting this user would remove (nothing is changed)."""
    conn = get_connection()
    try:
        counts: dict[str, int] = {}
        for label, kind, where, args in _plan(conn, user_id, remove_from_other_contacts):
            table = label.split(".")[0]
            n = _count(conn, table, where, args)
            if n:
                counts[label if kind == "null" else table] = counts.get(label if kind == "null" else table, 0) + n
        return counts
    finally:
        conn.close()


def delete_user(user_id: int, remove_from_other_contacts: bool = False) -> dict[str, int]:
    """Deletes everything about the user in ONE transaction and returns the per-table counts. Does not touch
    Google (see google_oauth.revoke_google_tokens) or backups."""
    conn = get_connection()
    try:
        counts: dict[str, int] = {}
        conn.execute("BEGIN")
        for label, kind, where, args in _plan(conn, user_id, remove_from_other_contacts):
            table = label.split(".")[0]
            if kind == "delete":
                cur = conn.execute(f"DELETE FROM {table} WHERE {where}", args)
            else:
                col = label.split(".")[1]
                cur = conn.execute(f"UPDATE {table} SET {col} = NULL WHERE {where}", args)
            if cur.rowcount:
                key = label if kind == "null" else table
                counts[key] = counts.get(key, 0) + cur.rowcount
        conn.commit()
        return counts
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

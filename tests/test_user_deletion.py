"""Full user deletion (scripts/delete_user.py -> src/user_deletion.py)."""
from unittest.mock import patch

from src import user_deletion
from src.db import models


def _fill(conn, table, overrides):
    """Inserts one row into `table`, giving every NOT NULL column without a default a plausible value."""
    cols = conn.execute(f"PRAGMA table_info({table})").fetchall()
    values = {}
    for c in cols:
        name, ctype, notnull, default, pk = c[1], (c[2] or "").upper(), c[3], c[4], c[5]
        if name in overrides:
            values[name] = overrides[name]
        elif pk and "INT" in ctype and name == "id":
            continue
        elif notnull and default is None:
            values[name] = 1 if "INT" in ctype else ("2026-01-01 00:00:00" if "TIME" in ctype else "x")
    names = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    cur = conn.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", tuple(values.values()))
    return cur.lastrowid


def _populate_every_user_table(user_id, contact_owner_number_in_other_book=None):
    """One row in every table that points at users (plus the contact those rows can reference)."""
    conn = models.get_connection()
    try:
        contact_id = _fill(conn, "contacts", {"owner_user_id": user_id, "whatsapp_number": "972500009999"})
        for table, col in user_deletion._tables_referencing(conn, "users"):
            if table == "contacts":
                continue
            overrides = {col: user_id}
            if any(fk[2] == "contacts" for fk in conn.execute(f"PRAGMA foreign_key_list({table})")):
                overrides["recipient_contact_id"] = contact_id
            _fill(conn, table, overrides)
        conn.commit()
    finally:
        conn.close()


def _rows_for(user_id):
    conn = models.get_connection()
    try:
        left = {}
        for table, col in user_deletion._tables_referencing(conn, "users"):
            if table == "admin_audit_log":
                continue
            n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", (user_id,)).fetchone()[0]
            if n:
                left[table] = n
        if conn.execute("SELECT COUNT(*) FROM users WHERE id = ?", (user_id,)).fetchone()[0]:
            left["users"] = 1
        return left
    finally:
        conn.close()


def test_every_table_that_points_at_users_is_emptied_and_the_other_user_is_untouched(make_user):
    """If a new table with a user foreign key is added and cannot be deleted, this fails."""
    target = make_user(whatsapp_number="972500000011", display_name="Leaver")
    other = make_user(whatsapp_number="972500000012", display_name="Stayer")
    _populate_every_user_table(target)
    _populate_every_user_table(other)
    assert _rows_for(target)  # the fixture really did fill tables

    counts = user_deletion.delete_user(target)

    assert _rows_for(target) == {}
    assert counts["users"] == 1
    assert counts.get("ai_preferences") == 1  # the per-user AI provider choice goes too
    other_left = _rows_for(other)
    assert other_left.get("users") == 1 and "messages" in other_left and "contacts" in other_left


def test_describe_changes_nothing_and_matches_what_delete_removes(make_user):
    target = make_user(whatsapp_number="972500000011")
    _populate_every_user_table(target)

    planned = user_deletion.describe(target)
    assert _rows_for(target)  # untouched
    deleted = user_deletion.delete_user(target)

    assert {k: v for k, v in planned.items() if "." not in k} == {k: v for k, v in deleted.items() if "." not in k}


def test_shadow_log_rows_are_removed_through_the_message_id(make_user):
    target = make_user(whatsapp_number="972500000011")
    conn = models.get_connection()
    try:
        _fill(conn, "messages", {"user_id": target, "whatsapp_message_id": "wamid.GONE"})
        _fill(conn, "intent_shadow_log", {"whatsapp_message_id": "wamid.GONE"})
        _fill(conn, "intent_shadow_log", {"whatsapp_message_id": "wamid.KEEP"})
        conn.commit()
    finally:
        conn.close()

    user_deletion.delete_user(target)

    conn = models.get_connection()
    try:
        ids = [r[0] for r in conn.execute("SELECT whatsapp_message_id FROM intent_shadow_log")]
    finally:
        conn.close()
    assert ids == ["wamid.KEEP"]


def test_audit_log_is_kept_but_no_longer_points_at_the_user(make_user):
    target = make_user(whatsapp_number="972500000011")
    models.log_admin_action("admin@example.test", "view_user_reminders", target_user_id=target)

    user_deletion.delete_user(target)

    conn = models.get_connection()
    try:
        row = conn.execute("SELECT target_user_id FROM admin_audit_log").fetchone()
    finally:
        conn.close()
    assert row is not None and row[0] is None


def test_contacts_option_removes_the_number_from_other_users_books_and_their_reminders(make_user):
    leaver = make_user(whatsapp_number="972500000011")
    friend = make_user(whatsapp_number="972500000012")
    conn = models.get_connection()
    try:
        in_friends_book = _fill(conn, "contacts", {"owner_user_id": friend, "name": "Leaver", "whatsapp_number": "972500000011"})
        other_contact = _fill(conn, "contacts", {"owner_user_id": friend, "name": "Other", "whatsapp_number": "972500000099"})
        _fill(conn, "reminders", {"user_id": friend, "recipient_contact_id": in_friends_book})
        _fill(conn, "reminders", {"user_id": friend, "recipient_contact_id": other_contact})
        conn.commit()
    finally:
        conn.close()

    without = user_deletion.describe(leaver, remove_from_other_contacts=False)
    assert "contacts" not in without
    user_deletion.delete_user(leaver, remove_from_other_contacts=True)

    conn = models.get_connection()
    try:
        numbers = [r[0] for r in conn.execute("SELECT whatsapp_number FROM contacts")]
        remaining = conn.execute("SELECT COUNT(*) FROM reminders").fetchone()[0]
    finally:
        conn.close()
    assert numbers == ["972500000099"] and remaining == 1


def test_a_failure_rolls_everything_back(make_user):
    target = make_user(whatsapp_number="972500000011")
    _populate_every_user_table(target)
    before = _rows_for(target)

    real_plan = user_deletion._plan

    def broken(conn, user_id, flag):
        return real_plan(conn, user_id, flag)[:-1] + [("no_such_table", "delete", "1 = 1", ())]

    with patch.object(user_deletion, "_plan", broken):
        try:
            user_deletion.delete_user(target)
        except Exception:
            pass
    assert _rows_for(target) == before


# ---- the CLI ---------------------------------------------------------------------------------------

def _cli(args, monkeypatch=None):
    import importlib.util
    import os

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "delete_user.py")
    spec = importlib.util.spec_from_file_location("delete_user_cli", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main(args)


def test_cli_dry_run_changes_nothing(make_user, capsys):
    target = make_user(whatsapp_number="972500000011")
    assert _cli(["972500000011", "--dry-run"]) == 0
    assert "dry run" in capsys.readouterr().out
    assert models.get_user_by_whatsapp_number("972500000011") is not None


def test_cli_refuses_to_delete_the_last_admin_without_force(make_user, capsys):
    make_user(whatsapp_number="972500000011", is_admin=True)
    assert _cli(["972500000011", "--yes", "--keep-google"]) == 1
    assert models.get_user_by_whatsapp_number("972500000011") is not None
    assert _cli(["972500000011", "--yes", "--keep-google", "--force"]) == 0
    assert models.get_user_by_whatsapp_number("972500000011") is None


def test_cli_wrong_confirmation_deletes_nothing(make_user, monkeypatch):
    make_user(whatsapp_number="972500000011")
    monkeypatch.setattr("builtins.input", lambda *_: "nope")
    assert _cli(["972500000011", "--keep-google"]) == 1
    assert models.get_user_by_whatsapp_number("972500000011") is not None


def test_cli_revokes_google_then_deletes_and_records_an_audit_entry(make_user):
    make_user(whatsapp_number="972500000099", is_admin=True)
    make_user(whatsapp_number="972500000011")
    with patch("src.integrations.google_oauth.revoke_google_tokens", return_value="revoked") as revoke:
        assert _cli(["972500000011", "--yes"]) == 0
    revoke.assert_called_once()
    assert models.get_user_by_whatsapp_number("972500000011") is None
    conn = models.get_connection()
    try:
        row = conn.execute("SELECT admin_email, action FROM admin_audit_log").fetchone()
    finally:
        conn.close()
    assert tuple(row) == ("cli:delete_user", "delete_user")


def test_cli_unknown_number_and_bad_format(make_user):
    assert _cli(["972500000077"]) == 1
    assert _cli(["+972500000077"]) == 1

"""
Regression test for step 0 of the function-calling migration: messages.
parsed_intent has existed in schema.sql since the first commit but was never
actually written until now - there was no way to build a before/after
regression baseline for a classifier change without it. This locks in that
save_incoming_message actually persists what it's given.
"""
from src.db.models import get_connection, save_incoming_message


def test_parsed_intent_is_stored(db_path, make_user):
    user_id = make_user()
    save_incoming_message(user_id, "מה מזג האוויר", "wamid.1", "text", parsed_intent="weather")

    conn = get_connection()
    row = conn.execute("SELECT parsed_intent FROM messages WHERE whatsapp_message_id = ?", ("wamid.1",)).fetchone()
    conn.close()
    assert row["parsed_intent"] == "weather"


def test_parsed_intent_omitted_stores_null(db_path, make_user):
    """Callers that predate this change (or a future call site that forgets
    to pass it) must not crash - and NULL, not some placeholder string, is
    what a baseline-building pass needs to find 'not yet classified'
    messages."""
    user_id = make_user()
    save_incoming_message(user_id, "hi", "wamid.2", "text")

    conn = get_connection()
    row = conn.execute("SELECT parsed_intent FROM messages WHERE whatsapp_message_id = ?", ("wamid.2",)).fetchone()
    conn.close()
    assert row["parsed_intent"] is None

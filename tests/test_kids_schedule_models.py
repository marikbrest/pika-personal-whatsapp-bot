"""
src.db.models's kids_schedule table (new feature, 2026-09-15) - the storage
side of a kid's recurring weekly timetable. See
tests/test_tool_batch12.py for the chat-driven CRUD tool built on top of
this, and tests/test_scheduler_kids_schedule.py for the nightly proactive
send that reads it back via get_kids_schedule_for_day/
list_users_with_kids_schedule.
"""
from src.db.models import (
    MAX_KIDS_SCHEDULE_ROWS_PER_USER,
    delete_kid_schedule_day,
    find_kid_schedule_owner_by_whatsapp_number,
    get_kid_schedule,
    get_kids_schedule_for_day,
    list_users_with_kids_schedule,
    save_contact,
    upsert_kid_schedule_day,
)


def test_upsert_creates_a_new_row(db_path, make_user):
    user_id = make_user()
    assert upsert_kid_schedule_day(user_id, "דני", "mon", "חשבון בשמונה") is True
    rows = get_kid_schedule(user_id, "דני")
    assert len(rows) == 1
    assert rows[0]["day_of_week"] == "mon"
    assert rows[0]["content"] == "חשבון בשמונה"


def test_upsert_on_the_same_kid_and_day_replaces_content_not_appends(db_path, make_user):
    user_id = make_user()
    upsert_kid_schedule_day(user_id, "דני", "mon", "חשבון בשמונה")
    upsert_kid_schedule_day(user_id, "דני", "mon", "אנגלית בעשר")
    rows = get_kid_schedule(user_id, "דני")
    assert len(rows) == 1
    assert rows[0]["content"] == "אנגלית בעשר"


def test_get_kid_schedule_scopes_by_kid_name(db_path, make_user):
    user_id = make_user()
    upsert_kid_schedule_day(user_id, "דני", "mon", "חשבון")
    upsert_kid_schedule_day(user_id, "נועה", "mon", "ציור")
    assert [r["kid_name"] for r in get_kid_schedule(user_id, "דני")] == ["דני"]
    assert {r["kid_name"] for r in get_kid_schedule(user_id)} == {"דני", "נועה"}


def test_get_kid_schedule_scopes_by_user(db_path, make_user):
    user_a = make_user(whatsapp_number="972500000001")
    user_b = make_user(whatsapp_number="972500000002")
    upsert_kid_schedule_day(user_a, "דני", "mon", "חשבון")
    assert get_kid_schedule(user_b, "דני") == []


def test_delete_one_day_only(db_path, make_user):
    user_id = make_user()
    upsert_kid_schedule_day(user_id, "דני", "mon", "חשבון")
    upsert_kid_schedule_day(user_id, "דני", "tue", "אנגלית")
    removed = delete_kid_schedule_day(user_id, "דני", "mon")
    assert removed == 1
    remaining = get_kid_schedule(user_id, "דני")
    assert len(remaining) == 1
    assert remaining[0]["day_of_week"] == "tue"


def test_delete_with_no_day_removes_the_whole_kid(db_path, make_user):
    user_id = make_user()
    upsert_kid_schedule_day(user_id, "דני", "mon", "חשבון")
    upsert_kid_schedule_day(user_id, "דני", "tue", "אנגלית")
    removed = delete_kid_schedule_day(user_id, "דני")
    assert removed == 2
    assert get_kid_schedule(user_id, "דני") == []


def test_delete_returns_zero_when_nothing_matched(db_path, make_user):
    user_id = make_user()
    assert delete_kid_schedule_day(user_id, "לא-קיים", "mon") == 0


def test_cap_blocks_a_new_row_but_not_editing_an_existing_one(db_path, make_user):
    user_id = make_user()
    for i in range(MAX_KIDS_SCHEDULE_ROWS_PER_USER):
        assert upsert_kid_schedule_day(user_id, f"kid{i}", "mon", "x") is True

    # at the cap: a brand new row is refused...
    assert upsert_kid_schedule_day(user_id, "one-too-many", "mon", "x") is False
    # ...but editing one of the existing rows still works, since it doesn't grow the table
    assert upsert_kid_schedule_day(user_id, "kid0", "mon", "updated") is True
    assert get_kid_schedule(user_id, "kid0")[0]["content"] == "updated"


def test_get_kids_schedule_for_day_groups_multiple_kids(db_path, make_user):
    user_id = make_user()
    upsert_kid_schedule_day(user_id, "דני", "wed", "חשבון")
    upsert_kid_schedule_day(user_id, "נועה", "wed", "ציור")
    upsert_kid_schedule_day(user_id, "דני", "thu", "התעלמות")  # different day, should not appear

    rows = get_kids_schedule_for_day(user_id, "wed")
    assert {r["kid_name"]: r["content"] for r in rows} == {"דני": "חשבון", "נועה": "ציור"}


def test_list_users_with_kids_schedule_only_includes_users_who_saved_something(db_path, make_user):
    user_with = make_user(whatsapp_number="972500000001")
    make_user(whatsapp_number="972500000002")  # never saves anything
    upsert_kid_schedule_day(user_with, "דני", "mon", "חשבון")

    users = list_users_with_kids_schedule()
    assert [u["user_id"] for u in users] == [user_with]
    assert users[0]["whatsapp_number"] == "972500000001"


def test_find_kid_schedule_owner_by_whatsapp_number_resolves_a_matching_contact(db_path, make_user):
    parent_id = make_user(whatsapp_number="972500000001")
    kid_number = "972500000072"
    save_contact(parent_id, "נועה", kid_number)
    upsert_kid_schedule_day(parent_id, "נועה", "mon", "חשבון")

    match = find_kid_schedule_owner_by_whatsapp_number(kid_number)
    assert match == (parent_id, "נועה")


def test_find_kid_schedule_owner_returns_none_when_no_contact_matches(db_path):
    assert find_kid_schedule_owner_by_whatsapp_number("972500000099") is None


def test_find_kid_schedule_owner_returns_none_when_contact_exists_but_no_schedule_saved(db_path, make_user):
    parent_id = make_user(whatsapp_number="972500000001")
    kid_number = "972500000072"
    save_contact(parent_id, "נועה", kid_number)
    # no upsert_kid_schedule_day call - contact exists, but nothing saved yet

    assert find_kid_schedule_owner_by_whatsapp_number(kid_number) is None


def test_find_kid_schedule_owner_prefers_a_match_that_actually_has_data(db_path, make_user):
    """Same kid saved under two different owners/spellings (e.g. Yossi's old
    'Or' contact next to a newer 'נועה') - only the one with real data resolves."""
    parent_a = make_user(whatsapp_number="972500000001")
    parent_b = make_user(whatsapp_number="972500000002")
    kid_number = "972500000072"

    save_contact(parent_a, "Or", kid_number)  # no schedule saved for this spelling
    save_contact(parent_b, "נועה", kid_number)
    upsert_kid_schedule_day(parent_b, "נועה", "mon", "חשבון")

    match = find_kid_schedule_owner_by_whatsapp_number(kid_number)
    assert match == (parent_b, "נועה")

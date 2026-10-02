"""
src.db.models's daily_meetings_summary_enabled opt-in flag (new feature,
2026-09-15) - the storage side of the automatic daily calendar summary. See
tests/test_tool_batch13.py for the chat-driven toggle tool and
tests/test_scheduler_daily_meetings_summary.py for the proactive morning
send that reads it back.
"""
from src.db.models import (
    admin_set_user_active,
    get_daily_meetings_summary_enabled,
    get_daily_meetings_summary_time,
    list_users_with_daily_meetings_summary_enabled,
    mark_daily_meetings_summary_sent,
    set_daily_meetings_summary_enabled,
    set_daily_meetings_summary_time,
)


def test_defaults_to_disabled(db_path, make_user):
    user_id = make_user()
    assert get_daily_meetings_summary_enabled(user_id) is False


def test_enable_then_disable(db_path, make_user):
    user_id = make_user()
    set_daily_meetings_summary_enabled(user_id, True)
    assert get_daily_meetings_summary_enabled(user_id) is True

    set_daily_meetings_summary_enabled(user_id, False)
    assert get_daily_meetings_summary_enabled(user_id) is False


def test_one_users_flag_does_not_affect_another(make_user):
    user_a = make_user(whatsapp_number="972500000001")
    user_b = make_user(whatsapp_number="972500000002")
    set_daily_meetings_summary_enabled(user_a, True)

    assert get_daily_meetings_summary_enabled(user_a) is True
    assert get_daily_meetings_summary_enabled(user_b) is False


def test_list_only_includes_enabled_users(make_user):
    enabled_user = make_user(whatsapp_number="972500000001")
    make_user(whatsapp_number="972500000002")  # never enables
    set_daily_meetings_summary_enabled(enabled_user, True)

    rows = list_users_with_daily_meetings_summary_enabled()
    assert [r["user_id"] for r in rows] == [enabled_user]
    assert rows[0]["whatsapp_number"] == "972500000001"


def test_list_excludes_a_disabled_user_who_opts_out_again(make_user):
    user_id = make_user()
    set_daily_meetings_summary_enabled(user_id, True)
    set_daily_meetings_summary_enabled(user_id, False)

    assert list_users_with_daily_meetings_summary_enabled() == []


def test_list_excludes_an_inactive_user_even_if_enabled(make_user):
    """A user disabled by admin (is_active=0) should not get proactive sends."""
    user_id = make_user()
    set_daily_meetings_summary_enabled(user_id, True)
    admin_set_user_active(user_id, False)

    assert list_users_with_daily_meetings_summary_enabled() == []


def test_time_defaults_to_0700(db_path, make_user):
    user_id = make_user()
    assert get_daily_meetings_summary_time(user_id) == "07:00"


def test_set_and_get_time(db_path, make_user):
    user_id = make_user()
    set_daily_meetings_summary_time(user_id, "09:00")
    assert get_daily_meetings_summary_time(user_id) == "09:00"


def test_one_users_time_does_not_affect_another(make_user):
    user_a = make_user(whatsapp_number="972500000001")
    user_b = make_user(whatsapp_number="972500000002")
    set_daily_meetings_summary_time(user_a, "09:00")

    assert get_daily_meetings_summary_time(user_a) == "09:00"
    assert get_daily_meetings_summary_time(user_b) == "07:00"


def test_list_includes_each_users_own_time_and_last_sent_date(make_user):
    user_id = make_user(whatsapp_number="972500000001")
    set_daily_meetings_summary_enabled(user_id, True)
    set_daily_meetings_summary_time(user_id, "09:00")
    mark_daily_meetings_summary_sent(user_id, "2026-09-17")

    rows = list_users_with_daily_meetings_summary_enabled()
    assert rows[0]["daily_meetings_summary_time"] == "09:00"
    assert rows[0]["daily_meetings_summary_last_sent_date"] == "2026-09-17"


def test_mark_sent_then_query_reflects_it(db_path, make_user):
    user_id = make_user(whatsapp_number="972500000001")
    set_daily_meetings_summary_enabled(user_id, True)
    mark_daily_meetings_summary_sent(user_id, "2026-09-17")

    rows = list_users_with_daily_meetings_summary_enabled()
    assert rows[0]["daily_meetings_summary_last_sent_date"] == "2026-09-17"

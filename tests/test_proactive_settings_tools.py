"""
_handle_manage_proactive_settings / _handle_manage_vip_senders (2026-09-27)
- the Gatekeeper's settings tools. Real DB fixtures (writes are the point
of these tools), no external calls to mock.
"""
from src.db.models import get_proactive_settings, list_vip_senders
from src.webhook_handler import _handle_manage_proactive_settings, _handle_manage_vip_senders


def _user(user_id=1):
    return {"id": user_id, "whatsapp_number": "972500000001", "timezone": "Asia/Jerusalem"}


# ===== manage_proactive_settings =====

def test_enable_creates_a_settings_row_with_defaults(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    reply = _handle_manage_proactive_settings(_user(), {"action": "enable"})

    settings = get_proactive_settings(1)
    assert settings["enabled"] == 1
    assert settings["quiet_hours_start"] == "22:30"
    assert settings["quiet_hours_end"] == "07:00"
    assert settings["daily_cap"] == 6
    assert "הפעלתי" in reply


def test_disable_after_enable(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    _handle_manage_proactive_settings(_user(), {"action": "enable"})
    reply = _handle_manage_proactive_settings(_user(), {"action": "disable"})

    assert get_proactive_settings(1)["enabled"] == 0
    assert "כיביתי" in reply


def test_status_when_never_enabled(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    reply = _handle_manage_proactive_settings(_user(), {"action": "status"})
    assert "כבוי" in reply


def test_status_when_enabled_shows_settings(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    _handle_manage_proactive_settings(_user(), {"action": "enable"})
    reply = _handle_manage_proactive_settings(_user(), {"action": "status"})
    assert "22:30" in reply
    assert "07:00" in reply
    assert "6" in reply
    assert "15" in reply  # default meeting_lead_time_minutes


def test_set_status_sets_a_future_quiet_until(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    _handle_manage_proactive_settings(_user(), {"action": "enable"})
    reply = _handle_manage_proactive_settings(_user(), {"action": "set_status", "minutes": 60})

    settings = get_proactive_settings(1)
    assert settings["status_quiet_until"] is not None
    assert "60" in reply


def test_set_status_without_minutes_asks_for_it():
    reply = _handle_manage_proactive_settings(_user(), {"action": "set_status"})
    assert "זמן" in reply


def test_clear_status(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    _handle_manage_proactive_settings(_user(), {"action": "enable"})
    _handle_manage_proactive_settings(_user(), {"action": "set_status", "minutes": 60})
    _handle_manage_proactive_settings(_user(), {"action": "clear_status"})

    assert get_proactive_settings(1)["status_quiet_until"] is None


def test_set_quiet_hours(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    _handle_manage_proactive_settings(_user(), {"action": "enable"})
    reply = _handle_manage_proactive_settings(
        _user(), {"action": "set_quiet_hours", "start": "23:00", "end": "06:00"},
    )

    settings = get_proactive_settings(1)
    assert settings["quiet_hours_start"] == "23:00"
    assert settings["quiet_hours_end"] == "06:00"
    assert "23:00" in reply


def test_set_daily_cap(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    _handle_manage_proactive_settings(_user(), {"action": "enable"})
    reply = _handle_manage_proactive_settings(_user(), {"action": "set_daily_cap", "cap": 3})

    assert get_proactive_settings(1)["daily_cap"] == 3
    assert "3" in reply


def test_set_meeting_lead_time(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    _handle_manage_proactive_settings(_user(), {"action": "enable"})
    reply = _handle_manage_proactive_settings(_user(), {"action": "set_meeting_lead_time", "lead_minutes": 10})

    assert get_proactive_settings(1)["meeting_lead_time_minutes"] == 10
    assert "10" in reply


def test_set_meeting_lead_time_without_a_value_asks_for_it():
    reply = _handle_manage_proactive_settings(_user(), {"action": "set_meeting_lead_time"})
    assert "דקות" in reply


# ===== manage_vip_senders =====

def test_add_vip_sender(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    reply = _handle_manage_vip_senders(_user(), {"action": "add", "identifier": "boss@example.com", "label": "בוס"})

    vips = list_vip_senders(1)
    assert len(vips) == 1
    assert vips[0]["identifier"] == "boss@example.com"
    assert vips[0]["label"] == "בוס"
    assert "boss@example.com" in reply


def test_list_vip_senders_empty(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    reply = _handle_manage_vip_senders(_user(), {"action": "list"})
    assert "אין לך" in reply


def test_list_vip_senders_with_entries(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    _handle_manage_vip_senders(_user(), {"action": "add", "identifier": "boss@example.com"})
    reply = _handle_manage_vip_senders(_user(), {"action": "list"})
    assert "boss@example.com" in reply


def test_remove_vip_sender(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    _handle_manage_vip_senders(_user(), {"action": "add", "identifier": "boss@example.com"})
    reply = _handle_manage_vip_senders(_user(), {"action": "remove", "identifier": "boss@example.com"})

    assert list_vip_senders(1) == []
    assert "הסרתי" in reply


def test_remove_a_vip_that_does_not_exist(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    reply = _handle_manage_vip_senders(_user(), {"action": "remove", "identifier": "nobody@example.com"})
    assert "לא מצאתי" in reply


def test_add_vip_resolves_a_bare_name_from_contacts(db_path, make_user):
    """Real bug found live 2026-09-27: 'תוסיף את רונית ל-VIP' classified with
    identifier='רונית' (a bare name), which would never match a real
    incoming sender. Must resolve against the user's own saved contacts."""
    from src.db.models import save_contact

    make_user(whatsapp_number="972500000001")
    save_contact(1, "רונית", "972500000002")

    reply = _handle_manage_vip_senders(_user(), {"action": "add", "identifier": "רונית"})

    vips = list_vip_senders(1)
    assert len(vips) == 1
    assert vips[0]["identifier"] == "972500000002"
    assert vips[0]["label"] == "רונית"
    assert "972500000002" in reply


def test_add_vip_with_an_unknown_name_asks_for_a_real_identifier(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    reply = _handle_manage_vip_senders(_user(), {"action": "add", "identifier": "מישהו לא מוכר"})

    assert list_vip_senders(1) == []
    assert "לא מצאתי" in reply


def test_add_vip_with_a_real_email_does_not_touch_contacts(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    _handle_manage_vip_senders(_user(), {"action": "add", "identifier": "boss@example.com"})

    assert list_vip_senders(1)[0]["identifier"] == "boss@example.com"


def test_remove_vip_resolves_a_bare_name_too(db_path, make_user):
    from src.db.models import save_contact

    make_user(whatsapp_number="972500000001")
    save_contact(1, "רונית", "972500000002")
    _handle_manage_vip_senders(_user(), {"action": "add", "identifier": "רונית"})

    reply = _handle_manage_vip_senders(_user(), {"action": "remove", "identifier": "רונית"})

    assert list_vip_senders(1) == []
    assert "הסרתי" in reply


def test_vip_lists_are_isolated_per_owner(db_path, make_user):
    make_user(whatsapp_number="972500000001")
    make_user(whatsapp_number="972500000002")
    _handle_manage_vip_senders(_user(1), {"action": "add", "identifier": "boss@example.com"})

    assert len(list_vip_senders(1)) == 1
    assert len(list_vip_senders(2)) == 0

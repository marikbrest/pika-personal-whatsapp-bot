"""
Three webhook_handler intents are restricted to is_admin=1 in application
code, a SEPARATE authorization boundary from the admin dashboard's own
Cloudflare-Access-header check (see test_security_admin_auth.py) - these
run for an already-allowlisted WhatsApp user (14.2), gating only what a
non-admin family member is allowed to ask the bot for: internal
infrastructure status, API cost, and bot-access management.
"""
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from src.db.models import get_user_by_id, get_user_by_whatsapp_number, log_api_usage
from src.webhook_handler import _handle_usage_status, _handle_user_manage, _handle_zabbix_status


@pytest.fixture()
def admin(make_user):
    return {"id": make_user(whatsapp_number="972500000001", is_admin=True), "is_admin": True, "timezone": "Asia/Jerusalem"}


@pytest.fixture()
def regular_user(make_user):
    return {"id": make_user(whatsapp_number="972500000002", is_admin=False), "is_admin": False, "timezone": "Asia/Jerusalem"}


# ---- zabbix_status ----

def test_zabbix_status_refuses_non_admin_without_touching_zabbix(regular_user):
    with patch("src.webhook_handler.get_active_problems") as mock_zbx:
        reply = _handle_zabbix_status(regular_user)
    assert "רק למנהל המערכת" in reply
    mock_zbx.assert_not_called()


def test_zabbix_status_reports_problems_and_appends_unifi_line(admin):
    fake_problems = [{"host": "h", "description": "d", "severity": "3", "since": datetime.now(timezone.utc)}]
    with patch("src.webhook_handler.get_active_problems", return_value=fake_problems), \
         patch("src.webhook_handler.get_network_status", return_value={"client_count": 5, "internet_up": True}):
        reply = _handle_zabbix_status(admin)
    assert "d" in reply  # the problem description
    assert "5" in reply and "UniFi" in reply  # the appended line


def test_zabbix_status_handles_not_configured_gracefully(admin):
    from src.integrations.zabbix import ZabbixNotConfiguredError
    from src.integrations.unifi import UnifiNotConfiguredError

    with patch("src.webhook_handler.get_active_problems", side_effect=ZabbixNotConfiguredError), \
         patch("src.webhook_handler.get_network_status", side_effect=UnifiNotConfiguredError):
        reply = _handle_zabbix_status(admin)
    assert "עדיין לא מוגדר" in reply
    assert "UniFi" not in reply  # silently omitted, not an error message


def test_zabbix_status_survives_unifi_failure_without_losing_the_zabbix_reply(admin):
    """12.3 - an unrelated integration failing must not take down the part
    that already succeeded."""
    with patch("src.webhook_handler.get_active_problems", return_value=[]), \
         patch("src.webhook_handler.get_network_status", side_effect=RuntimeError("unifi is down")):
        reply = _handle_zabbix_status(admin)
    assert "הכל תקין" in reply


# ---- usage_status ----

def test_usage_status_refuses_non_admin(regular_user, db_path):
    reply = _handle_usage_status(regular_user)
    assert "רק למנהל המערכת" in reply


def test_usage_status_reports_real_logged_usage(admin, db_path):
    log_api_usage("gemini_generate", input_tokens=1000, output_tokens=50)
    log_api_usage("gemini_generate", input_tokens=2000, output_tokens=100)
    log_api_usage("gemini_embed", input_tokens=300)
    log_api_usage("ship24")
    log_api_usage("ship24")

    reply = _handle_usage_status(admin)

    assert "Gemini" in reply
    assert "Ship24" in reply
    assert "2/100" in reply  # 2 calls this month against the 100 quota
    assert "98" in reply  # remaining


def test_usage_status_omits_real_billing_line_when_not_available(admin, db_path):
    """Not configured, or export hasn't produced its first row yet (both
    return None from get_month_to_date_cost) - the report must not show a
    broken or misleading real-cost line in either case, just omit it."""
    with patch("src.integrations.gcp_billing.get_month_to_date_cost", return_value=None):
        reply = _handle_usage_status(admin)
    assert "עלות אמיתית" not in reply


def test_usage_status_includes_real_billing_line_when_available(admin, db_path):
    with patch("src.integrations.gcp_billing.get_month_to_date_cost", return_value={"total": 5.21, "currency": "ILS"}):
        reply = _handle_usage_status(admin)
    assert "5.21" in reply
    assert "ILS" in reply
    assert "עלות אמיתית" in reply


# ---- user_manage ----

def test_user_manage_refuses_non_admin(regular_user):
    reply = _handle_user_manage(regular_user, {"action": "list"})
    assert "רק למנהל המערכת" in reply


def test_user_manage_list_shows_all_users(admin, regular_user):
    reply = _handle_user_manage(admin, {"action": "list"})
    assert "972500000001" in reply and "972500000002" in reply


def test_user_manage_add_normalizes_israeli_local_number(admin):
    reply = _handle_user_manage(admin, {"action": "add", "whatsapp_number": "0501234567", "display_name": "Gil"})
    assert "972501234567" in reply
    assert get_user_by_whatsapp_number("972501234567") is not None


def test_user_manage_add_rejects_an_implausible_number(admin):
    reply = _handle_user_manage(admin, {"action": "add", "whatsapp_number": "123", "display_name": "x"})
    assert "לא נראה תקין" in reply


def test_user_manage_add_reactivates_a_previously_disabled_user(admin, make_user):
    user_id = make_user(whatsapp_number="972500000009", is_active=False)
    reply = _handle_user_manage(admin, {"action": "add", "whatsapp_number": "972500000009"})
    assert "הפעלתי מחדש" in reply
    assert get_user_by_id(user_id)["is_active"] == 1


def test_user_manage_add_refuses_a_number_already_active(admin, regular_user):
    reply = _handle_user_manage(admin, {"action": "add", "whatsapp_number": "972500000002"})
    assert "כבר משתמש פעיל" in reply


def test_user_manage_disable_refuses_to_lock_out_the_calling_admin(admin):
    """The one safety property this handler exists specifically to protect,
    per its own docstring: an admin cannot disable themselves."""
    reply = _handle_user_manage(admin, {"action": "disable", "whatsapp_number": "972500000001"})
    assert "לא אשבית אותך" in reply
    assert get_user_by_id(admin["id"])["is_active"] == 1


def test_user_manage_disable_works_on_someone_else(admin, regular_user):
    reply = _handle_user_manage(admin, {"action": "disable", "whatsapp_number": "972500000002"})
    assert "כבר לא יכול להשתמש" in reply
    assert get_user_by_id(regular_user["id"])["is_active"] == 0


def test_user_manage_disable_unknown_number_says_so(admin):
    reply = _handle_user_manage(admin, {"action": "disable", "whatsapp_number": "972599999999"})
    assert "לא מצאתי משתמש" in reply

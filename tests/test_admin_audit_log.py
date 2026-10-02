"""
admin_audit_log (2026-09-26) - a real, provable record of every admin action
that touches another user's data, so "do you see my messages" has an actual
documented answer instead of just Yossi's word. Covers the DB layer
(log_admin_action/list_admin_audit_log) and that the real admin routes
actually write a row when they touch user data.
"""
from src.db.models import list_admin_audit_log, log_admin_action

from .conftest import ADMIN_ALLOWED_EMAIL, ADMIN_HOST

_AUTH_HEADERS = {"Host": ADMIN_HOST, "Cf-Access-Authenticated-User-Email": ADMIN_ALLOWED_EMAIL}


# ===== DB layer =====

def test_log_admin_action_and_list_it_back(db_path, make_user):
    user_id = make_user(whatsapp_number="972500000002", display_name="רונית")
    log_admin_action(ADMIN_ALLOWED_EMAIL, "view_user_reminders", target_user_id=user_id)

    rows = list_admin_audit_log()
    assert len(rows) == 1
    assert rows[0]["admin_email"] == ADMIN_ALLOWED_EMAIL
    assert rows[0]["action"] == "view_user_reminders"
    assert rows[0]["target_display_name"] == "רונית"


def test_list_admin_audit_log_is_newest_first(db_path, make_user):
    log_admin_action(ADMIN_ALLOWED_EMAIL, "view_raw_logs")
    log_admin_action(ADMIN_ALLOWED_EMAIL, "add_user", details="whatsapp_number=972500000009")

    rows = list_admin_audit_log()
    assert [r["action"] for r in rows] == ["add_user", "view_raw_logs"]


def test_action_with_no_target_user_has_none_display_name(db_path, make_user):
    log_admin_action(ADMIN_ALLOWED_EMAIL, "view_raw_logs")
    rows = list_admin_audit_log()
    assert rows[0]["target_user_id"] is None
    assert rows[0]["target_display_name"] is None


# ===== Real admin routes actually log =====

def test_viewing_a_users_reminders_is_logged(client, make_user):
    user_id = make_user(whatsapp_number="972500000002", display_name="רונית")
    client.get(f"/admin/users/{user_id}/reminders", headers=_AUTH_HEADERS)

    rows = list_admin_audit_log()
    assert any(r["action"] == "view_user_reminders" and r["target_user_id"] == user_id for r in rows)


def test_adding_a_user_is_logged(client, make_user):
    client.post(
        "/admin/users/add", headers=_AUTH_HEADERS,
        data={"display_name": "אורח", "whatsapp_number": "0501234567"},
    )
    rows = list_admin_audit_log()
    assert any(r["action"] == "add_user" for r in rows)


def test_toggling_a_user_is_logged(client, make_user):
    user_id = make_user(whatsapp_number="972500000002", display_name="רונית")
    client.post("/admin/users/toggle", headers=_AUTH_HEADERS, data={"user_id": user_id, "is_active": 0})

    rows = list_admin_audit_log()
    assert any(r["action"] == "toggle_user_active" and r["target_user_id"] == user_id for r in rows)


def test_viewing_raw_logs_is_logged(client):
    client.get("/admin/logs", headers=_AUTH_HEADERS)
    rows = list_admin_audit_log()
    assert any(r["action"] == "view_raw_logs" for r in rows)


def test_viewing_the_audit_page_itself_is_not_logged(client):
    """The audit trail's own viewer never touches another user's data - logging
    every view of it would just be noise, not real transparency."""
    client.get("/admin/audit", headers=_AUTH_HEADERS)
    assert list_admin_audit_log() == []


def test_unauthorized_requests_are_not_logged(client, make_user):
    user_id = make_user(whatsapp_number="972500000002", display_name="רונית")
    resp = client.get(f"/admin/users/{user_id}/reminders")  # no auth headers at all
    assert resp.status_code == 403
    assert list_admin_audit_log() == []


def test_audit_page_renders_logged_rows(client, make_user):
    user_id = make_user(whatsapp_number="972500000002", display_name="רונית")
    client.get(f"/admin/users/{user_id}/reminders", headers=_AUTH_HEADERS)

    resp = client.get("/admin/audit", headers=_AUTH_HEADERS)
    assert resp.status_code == 200
    assert "view_user_reminders" in resp.text
    assert "רונית" in resp.text

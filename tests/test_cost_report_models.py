"""
cost_report_state (2026-09-27) - the cost-guard Yossi asked for before any
proactive/unattended feature gets built: a persisted (not in-memory) marker
so the "every 2 days" report and the budget alert survive restarts, unlike
an in-memory alert cooldown, which resets on every restart.
"""
from src.db.models import get_cost_report_state, mark_budget_alert_sent, mark_cost_report_sent


def test_cost_report_state_row_exists_with_null_fields_by_default(db_path):
    state = get_cost_report_state()
    assert state["id"] == 1
    assert state["last_report_sent_date"] is None
    assert state["last_budget_alert_sent_at"] is None


def test_mark_cost_report_sent_updates_the_date(db_path):
    mark_cost_report_sent("2026-09-27")
    assert get_cost_report_state()["last_report_sent_date"] == "2026-09-27"


def test_mark_budget_alert_sent_sets_a_timestamp(db_path):
    mark_budget_alert_sent()
    assert get_cost_report_state()["last_budget_alert_sent_at"] is not None

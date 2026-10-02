"""
check_and_send_cost_report (2026-09-27) - the cost-guard Yossi asked for
before any proactive/unattended feature gets built on this bot: a real
cost report every 2 days, plus an immediate alert if month-to-date spend
crosses COST_ALERT_BUDGET_USD, both sent ONLY to OWNER_WHATSAPP_NUMBER
(never "every admin", unlike check_google_token_health).
"""
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.db.models import get_cost_report_state, mark_budget_alert_sent, mark_cost_report_sent
from src.scheduler import check_and_send_cost_report

TZ = ZoneInfo("Asia/Jerusalem")
OWNER = "972500000001"


def _today_str():
    return datetime.now(TZ).date().isoformat()


def _days_ago_str(n):
    return (datetime.now(TZ).date() - timedelta(days=n)).isoformat()


# ===== not configured =====

def test_does_nothing_when_owner_number_is_not_configured(db_path):
    with patch("src.config.OWNER_WHATSAPP_NUMBER", ""), \
         patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_and_send_cost_report()

    mock_send.assert_not_called()


# ===== periodic report cadence =====

def test_sends_the_report_on_first_ever_run(db_path):
    with patch("src.config.OWNER_WHATSAPP_NUMBER", OWNER), \
         patch("src.integrations.gcp_billing.get_month_to_date_cost", return_value=None), \
         patch("src.integrations.whatsapp.send_text_message", return_value=True) as mock_send:
        check_and_send_cost_report()

    assert mock_send.call_args.kwargs["to"] == OWNER
    assert "דוח שימוש ועלות" in mock_send.call_args.kwargs["body"]
    assert get_cost_report_state()["last_report_sent_date"] == _today_str()


def test_does_not_resend_the_report_one_day_after_the_last_one(db_path):
    mark_cost_report_sent(_days_ago_str(1))
    with patch("src.config.OWNER_WHATSAPP_NUMBER", OWNER), \
         patch("src.integrations.gcp_billing.get_month_to_date_cost", return_value=None), \
         patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_and_send_cost_report()

    mock_send.assert_not_called()


def test_resends_the_report_two_days_after_the_last_one(db_path):
    mark_cost_report_sent(_days_ago_str(2))
    with patch("src.config.OWNER_WHATSAPP_NUMBER", OWNER), \
         patch("src.integrations.gcp_billing.get_month_to_date_cost", return_value=None), \
         patch("src.integrations.whatsapp.send_text_message", return_value=True) as mock_send:
        check_and_send_cost_report()

    mock_send.assert_called_once()
    assert get_cost_report_state()["last_report_sent_date"] == _today_str()


# ===== budget guard =====

def test_no_budget_alert_when_under_threshold(db_path):
    mark_cost_report_sent(_today_str())  # report already sent today, isolate the budget check
    with patch("src.config.OWNER_WHATSAPP_NUMBER", OWNER), \
         patch("src.config.COST_ALERT_BUDGET_USD", 15.0), \
         patch("src.integrations.gcp_billing.get_month_to_date_cost", return_value={"total": 5.0, "currency": "USD"}), \
         patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_and_send_cost_report()

    mock_send.assert_not_called()


def test_budget_alert_fires_when_real_billing_crosses_the_threshold(db_path):
    mark_cost_report_sent(_today_str())
    with patch("src.config.OWNER_WHATSAPP_NUMBER", OWNER), \
         patch("src.config.COST_ALERT_BUDGET_USD", 15.0), \
         patch("src.integrations.gcp_billing.get_month_to_date_cost", return_value={"total": 20.0, "currency": "USD"}), \
         patch("src.integrations.whatsapp.send_text_message", return_value=True) as mock_send:
        check_and_send_cost_report()

    mock_send.assert_called_once()
    assert mock_send.call_args.kwargs["to"] == OWNER
    assert "20.00" in mock_send.call_args.kwargs["body"]
    assert "15.00" in mock_send.call_args.kwargs["body"]
    assert get_cost_report_state()["last_budget_alert_sent_at"] is not None


def test_budget_alert_falls_back_to_the_token_estimate_when_real_billing_unavailable(db_path):
    mark_cost_report_sent(_today_str())
    with patch("src.config.OWNER_WHATSAPP_NUMBER", OWNER), \
         patch("src.config.COST_ALERT_BUDGET_USD", 1.0), \
         patch("src.integrations.gcp_billing.get_month_to_date_cost", return_value=None), \
         patch("src.webhook_handler._estimated_month_cost_usd", return_value=2.5), \
         patch("src.integrations.whatsapp.send_text_message", return_value=True) as mock_send:
        check_and_send_cost_report()

    mock_send.assert_called_once()
    assert "2.50" in mock_send.call_args.kwargs["body"]


def test_budget_alert_does_not_repeat_within_24_hours(db_path):
    mark_cost_report_sent(_today_str())
    mark_budget_alert_sent()
    with patch("src.config.OWNER_WHATSAPP_NUMBER", OWNER), \
         patch("src.config.COST_ALERT_BUDGET_USD", 1.0), \
         patch("src.integrations.gcp_billing.get_month_to_date_cost", return_value={"total": 50.0, "currency": "USD"}), \
         patch("src.integrations.whatsapp.send_text_message") as mock_send:
        check_and_send_cost_report()

    mock_send.assert_not_called()

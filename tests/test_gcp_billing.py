"""
src.integrations.gcp_billing - real Google Cloud billing costs via the
BigQuery billing export enabled 2026-09-14. Mocks the BigQuery client
directly rather than hitting the real (billed, and slow) API - the export
itself was verified live via the Cloud Console/BigQuery UI during setup,
not via these unit tests.
"""
from unittest.mock import MagicMock, patch

from src.integrations import gcp_billing


def test_get_month_to_date_cost_returns_none_when_not_configured():
    with patch("src.integrations.gcp_billing.GCP_BILLING_SA_KEY_PATH", None):
        assert gcp_billing.get_month_to_date_cost() is None


def test_get_month_to_date_cost_returns_none_on_query_failure():
    """Covers both a real failure and the expected, normal 'export hasn't
    produced its first row yet' state (a missing-table error) - both must
    be non-fatal, since a genuine Google Cloud outage or a still-empty
    export must never break the whole usage_status report."""
    fake_client = MagicMock()
    fake_client.query.side_effect = RuntimeError("table not found")
    with patch("src.integrations.gcp_billing._get_client", return_value=fake_client):
        assert gcp_billing.get_month_to_date_cost() is None


def test_get_month_to_date_cost_returns_the_real_total():
    fake_row = {"total": 5.21, "currency": "ILS"}
    fake_result = MagicMock()
    fake_result.result.return_value = [fake_row]
    fake_client = MagicMock()
    fake_client.query.return_value = fake_result

    with patch("src.integrations.gcp_billing._get_client", return_value=fake_client):
        result = gcp_billing.get_month_to_date_cost()

    assert result == {"total": 5.21, "currency": "ILS"}


def test_get_month_to_date_cost_returns_zero_when_no_rows_at_all():
    """No spend at all this month (e.g. right after the billing cycle
    rolled over) is a real, valid zero - not a failure."""
    fake_result = MagicMock()
    fake_result.result.return_value = []
    fake_client = MagicMock()
    fake_client.query.return_value = fake_result

    with patch("src.integrations.gcp_billing._get_client", return_value=fake_client):
        result = gcp_billing.get_month_to_date_cost()

    assert result == {"total": 0.0, "currency": "ILS"}


def test_get_month_to_date_cost_sums_multiple_currency_rows_instead_of_dropping_them():
    """Regression test for a real bug (found 2026-09-14 by a same-day
    bug-hunt review): this used to silently return only rows[0] and discard
    any other currency's total with no error or log - the report would show
    an artificially low total with no clue why."""
    fake_result = MagicMock()
    fake_result.result.return_value = [
        {"total": 5.0, "currency": "ILS"},
        {"total": 1.0, "currency": "USD"},
    ]
    fake_client = MagicMock()
    fake_client.query.return_value = fake_result

    with patch("src.integrations.gcp_billing._get_client", return_value=fake_client):
        result = gcp_billing.get_month_to_date_cost()

    assert result == {"total": 6.0, "currency": "ILS"}  # summed, not silently dropped to just 5.0


def test_query_targets_the_correct_billing_account_table():
    """Google's fixed naming convention: gcp_billing_export_v1_<account id
    with dashes replaced by underscores> - a typo here silently queries a
    table that doesn't exist, always returning None with no clear signal
    why, so it's worth its own explicit test."""
    fake_result = MagicMock()
    fake_result.result.return_value = []
    fake_client = MagicMock()
    fake_client.query.return_value = fake_result

    with patch("src.integrations.gcp_billing._get_client", return_value=fake_client):
        gcp_billing.get_month_to_date_cost()

    query_sent = fake_client.query.call_args.args[0]
    assert "gcp_billing_export_v1_018AA3_EFD7A9_6D2E58" in query_sent

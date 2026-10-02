"""
Real Google Cloud billing costs (2026-09-14), via the standard usage cost
BigQuery export enabled the same day on the Gemini API key's own project
(gen-lang-client-0037767834, billing account "My Billing Account" -
018AA3-EFD7A9-6D2E58) - see webhook_handler._handle_usage_status for how
this is presented alongside the existing token-based estimate.

A dedicated service account (whatsapp-bot-billing-reader), not reused from
anywhere else in this codebase: scoped to exactly two roles on this one
project (BigQuery Data Viewer, BigQuery Job User) via its own JSON key
(credentials/gcp_billing_service_account.json, gitignored) - it cannot read
or do anything outside that one export table, unlike the Gmail/Calendar
OAuth credentials, which are the user's own broad personal grant.

Billing export is NOT retroactive - the export table only starts existing
once the first daily export actually lands (Google's own documented
latency: usually within ~24 hours of enabling export, not instant). A
missing table is the expected, normal state for the first day, not an
error - get_month_to_date_cost treats that the same as any other query
failure: log and return None, letting the caller fall back to the
token-based estimate that already existed before this.
"""
from src.config import GCP_BILLING_DATASET, GCP_BILLING_PROJECT_ID, GCP_BILLING_SA_KEY_PATH

# Google's own fixed naming convention for the standard usage cost export
# table: gcp_billing_export_v1_<BILLING_ACCOUNT_ID, dashes replaced by underscores>.
_BILLING_ACCOUNT_ID = "018AA3-EFD7A9-6D2E58"
_TABLE_NAME = f"gcp_billing_export_v1_{_BILLING_ACCOUNT_ID.replace('-', '_')}"


class BillingNotConfiguredError(Exception):
    """Raised when the service account key path or project/dataset env vars are missing."""


def _get_client():
    from google.cloud import bigquery
    from google.oauth2 import service_account

    if not (GCP_BILLING_SA_KEY_PATH and GCP_BILLING_PROJECT_ID and GCP_BILLING_DATASET):
        raise BillingNotConfiguredError("GCP_BILLING_* env vars are not fully set")
    credentials = service_account.Credentials.from_service_account_file(GCP_BILLING_SA_KEY_PATH)
    return bigquery.Client(project=GCP_BILLING_PROJECT_ID, credentials=credentials)


def get_month_to_date_cost() -> dict | None:
    """
    Returns {"total": float, "currency": str} for the current calendar
    month's real Google Cloud cost (net of credits, matching the AI
    Studio "Spend" page's own number), or None if the export table does
    not exist yet, the client isn't configured, or the query otherwise
    fails - all treated the same way (non-fatal, logged, caller falls back).

    Credits (promotional/free-tier) are stored as their own negative-amount
    rows in BigQuery's billing export schema, not netted into `cost` itself -
    summing them in is Google's own documented pattern for "what did I
    actually pay", not a guess.
    """
    try:
        client = _get_client()
    except BillingNotConfiguredError as e:
        print(f"[gcp_billing] not configured: {e}")
        return None

    query = f"""
        SELECT
            SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) AS c), 0)) AS total,
            currency
        FROM `{GCP_BILLING_PROJECT_ID}.{GCP_BILLING_DATASET}.{_TABLE_NAME}`
        WHERE DATE(usage_start_time) >= DATE_TRUNC(CURRENT_DATE(), MONTH)
        GROUP BY currency
    """
    try:
        rows = list(client.query(query).result())
    except Exception as e:
        print(f"[gcp_billing] month-to-date cost query failed (expected until export's first daily run lands): {e}")
        return None

    if not rows:
        return {"total": 0.0, "currency": "ILS"}

    if len(rows) > 1:
        # Bug fix (2026-09-14, found by a same-day bug-hunt review): this
        # used to silently return only rows[0] and discard any other
        # currency's total with no error or log - if the export ever
        # contains line items in more than one currency (documented as
        # theoretically possible for GCP billing exports, e.g. differently-
        # denominated credits), the report would show an artificially low
        # total with no clue why. This billing account is expected to only
        # ever bill in one currency (confirmed live via AI Studio's own
        # Spend page showing ILS), so summing everything together and
        # reporting under the first row's currency is a reasonable,
        # visible-in-logs fallback rather than a silent data loss.
        print(
            f"[gcp_billing] warning: billing export returned {len(rows)} currency rows "
            f"({[r['currency'] for r in rows]}) - summing all into one total under {rows[0]['currency']}"
        )
        total = sum(float(r["total"] or 0.0) for r in rows)
        return {"total": total, "currency": rows[0]["currency"]}

    row = rows[0]
    return {"total": float(row["total"] or 0.0), "currency": row["currency"]}

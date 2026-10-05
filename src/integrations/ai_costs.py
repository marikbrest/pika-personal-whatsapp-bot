"""
Estimated OpenAI spend for the usage report and the cost guard.

Prices come from the operator's `.env` (OPENAI_PRICE_*_PER_M, USD per million tokens) because they change and differ by
model; this file does not hard-code any. If input/output prices are not set, every call is reported as UNPRICED rather
than silently counted as free. Voice transcription is always unpriced (billed per minute, not per token).
"""
from src import config
from src.db.models import get_ai_usage_summary


def prices_configured() -> bool:
    return config.OPENAI_PRICE_INPUT_PER_M is not None and config.OPENAI_PRICE_OUTPUT_PER_M is not None


def openai_month_cost() -> tuple[float, int]:
    """(estimated USD this month, number of calls that could not be priced)."""
    rows = get_ai_usage_summary("openai")
    if not prices_configured():
        return 0.0, sum(r["calls"] for r in rows)
    inp = config.OPENAI_PRICE_INPUT_PER_M or 0.0
    out = config.OPENAI_PRICE_OUTPUT_PER_M or 0.0
    cached_price = config.OPENAI_PRICE_CACHED_INPUT_PER_M if config.OPENAI_PRICE_CACHED_INPUT_PER_M is not None else inp
    web_price = config.OPENAI_PRICE_WEB_SEARCH_PER_CALL or 0.0
    total, unknown = 0.0, 0
    for r in rows:
        unknown += r["unknown_calls"]
        # Only the configured chat model is priced; any other model name (e.g. transcription) is unpriced.
        if r["model"] != config.OPENAI_MODEL and not str(r["model"]).startswith(config.OPENAI_MODEL + "-"):
            unknown += r["calls"] - r["unknown_calls"]
            continue
        total += ((r["input_tokens"] - r["cached_tokens"]) * inp + r["cached_tokens"] * cached_price + r["output_tokens"] * out) / 1_000_000
        total += r["web_calls"] * web_price
    return total, unknown


from src.i18n import t


def openai_report() -> str:
    """Empty string when OpenAI was never used, so installs without it see no change in the report."""
    rows = get_ai_usage_summary("openai")
    calls = sum(r["calls"] for r in rows)
    if not calls:
        return ""
    cost, unknown = openai_month_cost()
    text = t("costs.openai_month", calls=calls, cost=cost)
    if unknown:
        text += t("costs.openai_unpriced", unknown=unknown)
    return text

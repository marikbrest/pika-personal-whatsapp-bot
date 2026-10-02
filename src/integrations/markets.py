"""
Stocks and crypto via the unofficial (but stable and widely used) Yahoo
Finance endpoint, with no API key. The same endpoint covers both stocks and
crypto (with a -USD suffix), so no second provider is needed.

WARNING: this is an unofficial endpoint (Yahoo shut down their official API in
2017). It works and is stable as of today, but Yahoo may change or block it
without notice. If that happens, switch to another provider (for example
Alpha Vantage, which requires an API key).
"""
import httpx

# Global client with connection pooling - same lesson as whatsapp.py/weather.py
_client = httpx.Client(
    timeout=10.0,
    limits=httpx.Limits(max_keepalive_connections=5, keepalive_expiry=120.0),
    headers={
        # Yahoo blocks requests with "suspicious" headers (detected as a bot).
        # Browser-like headers significantly reduce the chance of being blocked.
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://finance.yahoo.com",
    },
)

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


class SymbolNotFoundError(Exception):
    """The symbol (stock/crypto) was not found on Yahoo Finance."""


def get_quote(symbol: str) -> dict:
    """
    Returns the current price for a symbol (a stock such as "AAPL" or
    "TEVA.TA", or crypto such as "BTC-USD" or "ETH-USD").
    Raises SymbolNotFoundError if the symbol does not exist.

    Returns: {symbol, price, previous_close, change, change_percent, currency}
    """
    resp = _client.get(CHART_URL.format(symbol=symbol), params={"range": "1d", "interval": "1d"})

    if resp.status_code == 404:
        raise SymbolNotFoundError(symbol)
    resp.raise_for_status()

    data = resp.json()
    chart = data.get("chart", {})
    if chart.get("error") or not chart.get("result"):
        raise SymbolNotFoundError(symbol)

    meta = chart["result"][0]["meta"]
    price = meta.get("regularMarketPrice")
    previous_close = meta.get("previousClose") or meta.get("chartPreviousClose")

    if price is None:
        raise SymbolNotFoundError(symbol)

    change = None
    change_percent = None
    if previous_close:
        change = price - previous_close
        change_percent = (change / previous_close) * 100

    return {
        "symbol": meta.get("symbol", symbol),
        "price": price,
        "previous_close": previous_close,
        "change": change,
        "change_percent": change_percent,
        "currency": meta.get("currency", ""),
    }


def format_quote_for_reply(quote: dict) -> str:
    """Formats the get_quote result as readable Hebrew text. Never goes through
    Gemini - this is a fact, not a guess."""
    price_line = f"{quote['price']:.2f} {quote['currency']}"

    if quote["change"] is not None:
        direction = "🟢" if quote["change"] >= 0 else "🔴"
        sign = "+" if quote["change"] >= 0 else ""
        change_line = f"{direction} {sign}{quote['change']:.2f} ({sign}{quote['change_percent']:.2f}%)"
        return f"📈 {quote['symbol']}: {price_line}\n{change_line}"

    return f"📈 {quote['symbol']}: {price_line}"

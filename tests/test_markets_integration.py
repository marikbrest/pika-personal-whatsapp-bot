"""
src.integrations.markets - no API key needed for this one (unofficial Yahoo
Finance endpoint), so every case is reachable by mocking _client.get with
real httpx.Response objects.
"""
from unittest.mock import patch

import httpx
import pytest

from src.integrations.markets import SymbolNotFoundError, format_quote_for_reply, get_quote


def _response(status_code: int, json_data: dict) -> httpx.Response:
    request = httpx.Request("GET", "https://query1.finance.yahoo.com/v8/finance/chart/AAPL")
    return httpx.Response(status_code, json=json_data, request=request)


def _chart_payload(symbol="AAPL", price=190.5, previous_close=188.0, currency="USD"):
    return {
        "chart": {
            "result": [{
                "meta": {
                    "symbol": symbol, "regularMarketPrice": price,
                    "previousClose": previous_close, "currency": currency,
                }
            }],
            "error": None,
        }
    }


def test_get_quote_computes_change_and_percent():
    with patch("src.integrations.markets._client.get", return_value=_response(200, _chart_payload())):
        quote = get_quote("AAPL")

    assert quote["symbol"] == "AAPL"
    assert quote["price"] == 190.5
    assert quote["change"] == pytest.approx(2.5)
    assert quote["change_percent"] == pytest.approx(2.5 / 188.0 * 100)


def test_get_quote_falls_back_to_chart_previous_close():
    payload = _chart_payload()
    del payload["chart"]["result"][0]["meta"]["previousClose"]
    payload["chart"]["result"][0]["meta"]["chartPreviousClose"] = 185.0
    with patch("src.integrations.markets._client.get", return_value=_response(200, payload)):
        quote = get_quote("AAPL")
    assert quote["previous_close"] == 185.0


def test_get_quote_with_no_previous_close_has_no_change():
    payload = _chart_payload()
    del payload["chart"]["result"][0]["meta"]["previousClose"]
    with patch("src.integrations.markets._client.get", return_value=_response(200, payload)):
        quote = get_quote("AAPL")
    assert quote["change"] is None
    assert quote["change_percent"] is None


def test_404_raises_symbol_not_found():
    with patch("src.integrations.markets._client.get", return_value=_response(404, {})):
        with pytest.raises(SymbolNotFoundError):
            get_quote("NOTREAL")


def test_chart_error_field_raises_symbol_not_found():
    payload = {"chart": {"result": None, "error": {"code": "Not Found"}}}
    with patch("src.integrations.markets._client.get", return_value=_response(200, payload)):
        with pytest.raises(SymbolNotFoundError):
            get_quote("NOTREAL")


def test_missing_price_raises_symbol_not_found():
    payload = _chart_payload()
    payload["chart"]["result"][0]["meta"]["regularMarketPrice"] = None
    with patch("src.integrations.markets._client.get", return_value=_response(200, payload)):
        with pytest.raises(SymbolNotFoundError):
            get_quote("AAPL")


def test_format_quote_for_reply_shows_direction_and_currency():
    up = format_quote_for_reply({"symbol": "AAPL", "price": 190.5, "change": 2.5, "change_percent": 1.33, "currency": "USD"})
    assert "🟢" in up and "+2.50" in up and "190.50 USD" in up

    down = format_quote_for_reply({"symbol": "TEVA.TA", "price": 40.0, "change": -1.0, "change_percent": -2.44, "currency": "ILS"})
    assert "🔴" in down and "-1.00" in down


def test_format_quote_for_reply_with_no_change_data():
    text = format_quote_for_reply({"symbol": "BTC-USD", "price": 60000.0, "change": None, "change_percent": None, "currency": "USD"})
    assert "BTC-USD" in text
    assert "🟢" not in text and "🔴" not in text

"""
Weather via Open-Meteo (open-meteo.com) - completely free, no API key.
Includes geocoding (city name -> coordinates) from the same provider, so there
is no extra key to manage.
"""
from datetime import datetime

import httpx

# Global client with connection pooling - same lesson as whatsapp.py: the
# module-level httpx.get() helper opens a new TCP+TLS connection every call.
_client = httpx.Client(timeout=10.0, limits=httpx.Limits(max_keepalive_connections=5, keepalive_expiry=120.0))

from src.i18n import current_locale, t

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Partial mapping of WMO Weather Codes (the standard Open-Meteo uses) to
# Hebrew. Full list: https://open-meteo.com/en/docs - not every code matters
# for a personal bot.
_WEATHER_CODES = frozenset([0, 1, 2, 3, 45, 48, 51, 53, 55, 61, 63, 65, 71, 73, 75, 80, 81, 82, 95])  # codes with a catalog entry: weather.code.<n>


class LocationNotFoundError(Exception):
    """No matching city was found for the given name."""


def _describe_code(code: int) -> str:
    return t(f"weather.code.{code}") if code in _WEATHER_CODES else t("weather.code_unknown", code=code)


def geocode(location: str) -> tuple[float, float, str]:
    """Returns (latitude, longitude, canonical name) for a city name.
    Raises LocationNotFoundError if not found."""
    resp = _client.get(GEOCODING_URL, params={"name": location, "count": 1, "language": current_locale()})
    resp.raise_for_status()
    results = resp.json().get("results")
    if not results:
        raise LocationNotFoundError(location)
    match = results[0]
    return match["latitude"], match["longitude"], match["name"]


def get_current_weather(location: str) -> dict:
    """
    Returns the current weather for a given city: {location, temperature,
    feels_like, description, wind_speed}.
    Raises LocationNotFoundError if the city was not found.
    """
    lat, lon, resolved_name = geocode(location)

    resp = _client.get(
        FORECAST_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            "timezone": "auto",
        },
    )
    resp.raise_for_status()
    current = resp.json()["current"]

    return {
        "location": resolved_name,
        "temperature": current["temperature_2m"],
        "feels_like": current["apparent_temperature"],
        "description": _describe_code(current["weather_code"]),
        "wind_speed": current["wind_speed_10m"],
    }


def get_daily_forecast(location: str, num_days: int) -> dict:
    """
    Returns the daily forecast for a given city, num_days ahead including today
    (index 0). Open-Meteo supports up to 16 days; we cap at 7 to keep WhatsApp
    replies short and focused.
    Raises LocationNotFoundError if the city was not found.

    Returns: {location, days: [{date, temp_max, temp_min, description, precipitation_probability}, ...]}
    """
    num_days = max(1, min(num_days, 7))
    lat, lon, resolved_name = geocode(location)

    resp = _client.get(
        FORECAST_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "timezone": "auto",
            "forecast_days": num_days,
        },
    )
    resp.raise_for_status()
    daily = resp.json()["daily"]

    days = []
    for i in range(len(daily["time"])):
        days.append(
            {
                "date": daily["time"][i],
                "temp_max": daily["temperature_2m_max"][i],
                "temp_min": daily["temperature_2m_min"][i],
                "description": _describe_code(daily["weather_code"][i]),
                "precipitation_probability": daily["precipitation_probability_max"][i],
            }
        )

    return {"location": resolved_name, "days": days}


def format_weather_for_reply(weather: dict) -> str:
    """Formats the get_current_weather result as readable text (in the current locale).
    Never goes through Gemini - this is a fact, not a guess."""
    return t(
        "weather.current", location=weather["location"], description=weather["description"],
        temperature=weather["temperature"], feels_like=weather["feels_like"], wind_speed=weather["wind_speed"],
    )


_WEEKDAY_CODES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]  # date.weekday() order


def _format_day_line(day: dict, include_date: bool = True) -> str:
    date = datetime.fromisoformat(day["date"])
    weekday = t(f"day.{_WEEKDAY_CODES[date.weekday()]}")
    label = t("weather.day_label", weekday=weekday, date=date.strftime("%d/%m")) if include_date else t("weather.today")
    rain = t("weather.rain_chance", percent=day["precipitation_probability"]) if day["precipitation_probability"] else ""
    return f"{label}: {day['description']}, {day['temp_min']:.0f}-{day['temp_max']:.0f}°C{rain}"


def format_forecast_for_reply(forecast: dict, day_offset: int, is_range: bool) -> str:
    """
    Formats a get_daily_forecast result as readable text (in the current locale).
    day_offset: which day the display starts at (0 = today).
    is_range: whether to show a range of days (e.g. "this week") or a single
    day (e.g. "tomorrow").
    """
    location = forecast["location"]
    days = forecast["days"][day_offset:]

    if not days:
        return t("weather.no_forecast", location=location)

    if not is_range:
        return t("weather.forecast_single", location=location, day=_format_day_line(days[0], include_date=(day_offset > 0)))

    lines = "\n".join(_format_day_line(d) for d in days)
    return t("weather.forecast_range", location=location, lines=lines)

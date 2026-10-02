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

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Partial mapping of WMO Weather Codes (the standard Open-Meteo uses) to
# Hebrew. Full list: https://open-meteo.com/en/docs - not every code matters
# for a personal bot.
_WEATHER_CODES = {
    0: "בהיר",
    1: "בהיר בעיקר",
    2: "מעונן חלקית",
    3: "מעונן",
    45: "ערפל",
    48: "ערפל קפוא",
    51: "טפטוף קל",
    53: "טפטוף",
    55: "טפטוף חזק",
    61: "גשם קל",
    63: "גשם",
    65: "גשם חזק",
    71: "שלג קל",
    73: "שלג",
    75: "שלג כבד",
    80: "ממטרים קלים",
    81: "ממטרים",
    82: "ממטרים חזקים",
    95: "סופת רעמים",
}


class LocationNotFoundError(Exception):
    """No matching city was found for the given name."""


def _describe_code(code: int) -> str:
    return _WEATHER_CODES.get(code, f"קוד מזג אוויר {code}")


def geocode(location: str) -> tuple[float, float, str]:
    """Returns (latitude, longitude, canonical name) for a city name.
    Raises LocationNotFoundError if not found."""
    resp = _client.get(GEOCODING_URL, params={"name": location, "count": 1, "language": "he"})
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
    """Formats the get_current_weather result as readable Hebrew text.
    Never goes through Gemini - this is a fact, not a guess."""
    return (
        f"🌤️ מזג אוויר ב{weather['location']}:\n"
        f"{weather['description']}, {weather['temperature']:.0f}°C "
        f"(מרגיש כמו {weather['feels_like']:.0f}°C)\n"
        f"💨 רוח: {weather['wind_speed']:.0f} קמ\"ש"
    )


_HEBREW_WEEKDAYS = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]


def _format_day_line(day: dict, include_date: bool = True) -> str:
    date = datetime.fromisoformat(day["date"])
    weekday = _HEBREW_WEEKDAYS[date.weekday()]
    label = f"יום {weekday} ({date.strftime('%d/%m')})" if include_date else "היום"
    rain = f", {day['precipitation_probability']:.0f}% סיכוי גשם" if day["precipitation_probability"] else ""
    return f"{label}: {day['description']}, {day['temp_min']:.0f}-{day['temp_max']:.0f}°C{rain}"


def format_forecast_for_reply(forecast: dict, day_offset: int, is_range: bool) -> str:
    """
    Formats a get_daily_forecast result as readable Hebrew text.
    day_offset: which day the display starts at (0 = today).
    is_range: whether to show a range of days (e.g. "this week") or a single
    day (e.g. "tomorrow").
    """
    location = forecast["location"]
    days = forecast["days"][day_offset:]

    if not days:
        return f"אין לי תחזית זמינה כל כך רחוק קדימה עבור {location}."

    if not is_range:
        return f"🌤️ תחזית ל{location} — {_format_day_line(days[0], include_date=(day_offset > 0))}"

    lines = "\n".join(_format_day_line(d) for d in days)
    return f"🌤️ תחזית ל{location}:\n{lines}"

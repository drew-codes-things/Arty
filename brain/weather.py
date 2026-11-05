"""Weather for the user's town, from Open-Meteo (free, no account or key).

The place is "weather" in brain.json: {"place": "London", "latitude": ..., "longitude": ...}.
"""
import json
import urllib.parse
import urllib.request
from datetime import date, datetime

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DEFAULT_PLACE = {"place": "London", "latitude": 51.507, "longitude": -0.128}
RAIN_WARNING_PERCENT = 50
MAX_DAYS = 7

# WMO weather codes (https://open-meteo.com/en/docs) in plain words.
CODES = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "foggy", 48: "foggy with frost",
         51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
         61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
         71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
         82: "heavy showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorms", 96: "thunderstorms with hail",
         99: "thunderstorms with hail"}


def answer(question: str, place: dict | None = None) -> str | None:
    """A direct answer to "will it rain tomorrow?" or "what's the weather on Friday?": the forecast line for the
    day asked about (today unless another is named), or None if the forecast can't be had."""
    lowered = question.lower()
    report = forecast(MAX_DAYS, place)
    if report.startswith("Couldn't"):
        return None
    lines = report.splitlines()
    wanted = "Tomorrow" if "tomorrow" in lowered else next(
        (day.title() for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday") if day in lowered), "Today")
    day_line = next((line for line in lines[1:] if line.startswith(wanted) or f"({wanted})" in line), None)
    if day_line is None:
        return None
    place_name = (place or DEFAULT_PLACE)["place"]
    now = lines[0].split("Now: ", 1)[-1] if wanted == "Today" else ""
    return f"{day_line.split(':', 1)[0]} in {place_name}:{day_line.split(':', 1)[1]}" + (f" Right now: {now}" if now else "")


def forecast(days=1, place: dict | None = None) -> str:
    """The weather tool: today's (or the next few days') forecast in words."""
    place = place or DEFAULT_PLACE
    try:
        days = max(1, min(int(days or 1), MAX_DAYS))
    except (TypeError, ValueError):
        days = 1
    query = urllib.parse.urlencode({
        "latitude": place["latitude"], "longitude": place["longitude"], "timezone": "Europe/London", "forecast_days": days,
        "current": "temperature_2m,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max",
        "wind_speed_unit": "mph",
    })
    try:
        with urllib.request.urlopen(f"{FORECAST_URL}?{query}", timeout=20) as response:
            data = json.loads(response.read())
    except (OSError, ValueError) as error:
        return f"Couldn't get the weather just now ({error})."
    now = data.get("current") or {}
    lines = [f"Weather for {place['place']}. Now: {round(now.get('temperature_2m', 0))}°C, "
             f"{CODES.get(now.get('weather_code'), 'unknown')}, wind {round(now.get('wind_speed_10m', 0))} mph."]
    daily = data.get("daily") or {}
    for index, day in enumerate(daily.get("time", [])):
        weekday = datetime.fromisoformat(day).strftime("%A")
        ahead = (date.fromisoformat(day) - date.today()).days
        label = "Today" if ahead == 0 else f"Tomorrow ({weekday})" if ahead == 1 else weekday
        rain = daily["precipitation_probability_max"][index] or 0
        lines.append(f"{label}: {CODES.get(daily['weather_code'][index], 'unknown')}, {round(daily['temperature_2m_min'][index])} to "
                     f"{round(daily['temperature_2m_max'][index])}°C, {rain}% chance of rain, wind up to {round(daily['wind_speed_10m_max'][index])} mph"
                     + (" (take a coat)" if rain >= RAIN_WARNING_PERCENT else "") + ".")
    return "\n".join(lines)

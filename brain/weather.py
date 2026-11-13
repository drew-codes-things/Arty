"""Weather for the user's town, from Open-Meteo (free, no account or key).

The place is "weather" in brain.json: {"place": "London", "latitude": ..., "longitude": ...}.
"""
import calendar
import json
import re
import urllib.parse
import urllib.request
from datetime import date, datetime

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
DEFAULT_PLACE = {"place": "London", "latitude": 51.507, "longitude": -0.128}
HOME_TIME_ZONE = "Europe/London"
RAIN_WARNING_PERCENT = 50
MAX_DAYS = 7

# WMO weather codes (https://open-meteo.com/en/docs) in plain words.
CODES = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "foggy", 48: "foggy with frost",
         51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
         61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
         71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
         82: "heavy showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorms", 96: "thunderstorms with hail",
         99: "thunderstorms with hail"}


MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
CLIMATE = re.compile(r"\b(usually|normally|typically|generally|on average|average|climate|this time of year|in (?:" + MONTHS + r"))\b",
                     re.IGNORECASE)
THERE = re.compile(r"(?<!\bis )(?<!\bare )(?<!\bwill )(?<!\bbe )\bthere\b(?!\s*(?:is|'s|are|will|be|was|were|any)\b)", re.IGNORECASE)
WEEKDAYS = tuple(name.lower() for name in calendar.day_name)
CALENDAR_WORDS = frozenset(
    WEEKDAYS + tuple(name.lower() for name in calendar.day_abbr) + ("tues", "thur", "thurs")
    + tuple(name.lower() for name in calendar.month_name[1:]) + tuple(name.lower() for name in calendar.month_abbr[1:]) + ("sept",)
    + ("christmas", "easter", "halloween"))
TIMES_OF_DAY = frozenset((
    "today", "tonight", "tomorrow", "tmrw", "yesterday", "morning", "afternoon", "evening", "night", "daytime", "noon", "midday",
    "midnight", "dawn", "dusk", "sunrise", "sunset", "breakfast", "brunch", "lunch", "lunchtime", "dinner", "dinnertime", "tea",
    "teatime", "weekend", "week", "fortnight", "month", "year", "day", "hour", "minute", "moment", "time", "times", "now", "then",
    "later", "once", "first", "last", "next"))
PEOPLE = frozenset(("me", "us", "you", "him", "her", "them", "it", "my", "our", "your", "his", "their", "its", "everyone",
                    "everybody", "anyone", "anybody", "someone", "somebody", "people"))
FILLER = frozenset(("the", "a", "an", "this", "that", "these", "those", "some", "any", "all", "each", "every", "half", "least",
                    "most", "bed", "work", "school", "college", "uni", "home", "town", "sure", "real", "example", "instance"))
NOT_A_PLACE = CALENDAR_WORDS | TIMES_OF_DAY | PEOPLE | FILLER
NOT_A_PLACE_PHRASES = ("new year", "bonfire night")
PLACE_ENDS = frozenset(("on", "at", "over", "right", "like", "be", "going", "for", "and", "or", "please", "is", "will", "in", "by",
                        "during"))
PLACE_AFTER = re.compile(r"\b(?:in|for|at)\s+(?=(?P<words>[a-z][\w'.-]*(?:\s+[a-z][\w'.-]*){0,2}))", re.IGNORECASE)


def is_not_a_place(word: str) -> bool:
    bare = word.lower().strip(".'")
    return bare in NOT_A_PLACE or bare[:1].isdigit()


def place_in(words: str) -> str | None:
    if words.lower().startswith(NOT_A_PLACE_PHRASES):
        return None
    first, *rest = words.split()
    if is_not_a_place(first):
        return None
    kept = [first]
    for word in rest:
        if word.lower() in PLACE_ENDS or is_not_a_place(word):
            break
        kept.append(word)
    return " ".join(kept).strip(" .'") or None


def named_place(question: str) -> str | None:
    found = (place_in(candidate.group("words")) for candidate in PLACE_AFTER.finditer(question or ""))
    return next((place for place in found if place), None)


def day_asked_about(question: str) -> str:
    lowered = question.lower()
    if "tomorrow" in lowered:
        return "Tomorrow"
    named = next((day for day in WEEKDAYS if day in lowered), None)
    if named is None or named == date.today().strftime("%A").lower():
        return "Today"
    return named.title()


def is_home(name: str, home: dict | None) -> bool:
    return name.lower() == str((home or DEFAULT_PLACE)["place"]).lower()


def not_a_home_forecast(question: str, home: dict | None = None) -> bool:
    named = named_place(question)
    return bool(CLIMATE.search(question or "") or THERE.search(question or "") or (named and not is_home(named, home)))


def find_place(name: str) -> dict | None:
    query = urllib.parse.urlencode({"name": name, "count": 1, "language": "en", "format": "json"})
    try:
        results = get_json(f"{GEOCODE_URL}?{query}").get("results") or []
    except (OSError, ValueError):
        return None
    if not results:
        return None
    found = results[0]
    label = ", ".join(part for part in (found.get("name"), found.get("country")) if part)
    return {"place": label, "latitude": found["latitude"], "longitude": found["longitude"],
            "timezone": found.get("timezone") or HOME_TIME_ZONE}


def place_asked_about(question: str, home: dict | None) -> dict | None:
    named = named_place(question)
    if not named or is_home(named, home):
        return home or DEFAULT_PLACE
    return find_place(named)


def answer(question: str, place: dict | None = None) -> str | None:
    """A direct answer to "will it rain tomorrow?" or "what's the weather on Friday?": the forecast line for the
    day asked about (today unless another is named), or None if the forecast can't be had."""
    if CLIMATE.search(question) or THERE.search(question):
        return None
    place = place_asked_about(question, place)
    if place is None:
        return None
    report = forecast(MAX_DAYS, place)
    if report.startswith("Couldn't"):
        return None
    lines = report.splitlines()
    wanted = day_asked_about(question)
    day_line = next((line for line in lines[1:] if line.startswith(wanted) or f"({wanted})" in line), None)
    if day_line is None:
        return None
    place_name = (place or DEFAULT_PLACE)["place"]
    now = lines[0].split("Now: ", 1)[-1] if wanted == "Today" else ""
    return f"{day_line.split(':', 1)[0]} in {place_name}:{day_line.split(':', 1)[1]}" + (f" Right now: {now}" if now else "")


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.loads(response.read())


def forecast(days=1, place: dict | None = None) -> str:
    """The weather tool: today's (or the next few days') forecast in words."""
    place = place or DEFAULT_PLACE
    try:
        days = max(1, min(int(days or 1), MAX_DAYS))
    except (TypeError, ValueError):
        days = 1
    query = urllib.parse.urlencode({
        "latitude": place["latitude"], "longitude": place["longitude"], "timezone": place.get("timezone") or HOME_TIME_ZONE,
        "forecast_days": days,
        "current": "temperature_2m,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max",
        "wind_speed_unit": "mph",
    })
    try:
        data = get_json(f"{FORECAST_URL}?{query}")
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

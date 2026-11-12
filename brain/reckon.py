import ast
import calendar
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

import calendar_feed
import quick

CURRENCY_URL = "https://api.frankfurter.dev/v1/latest"
CURRENCY_TIMEOUT_SECONDS = 15

LENGTH = {"mm": 0.001, "cm": 0.01, "m": 1, "km": 1000, "in": 0.0254, "ft": 0.3048, "yd": 0.9144, "mi": 1609.344}
MASS = {"mg": 0.000001, "g": 0.001, "kg": 1, "t": 1000, "oz": 0.028349523125, "lb": 0.45359237, "st": 6.35029318}
VOLUME = {"ml": 0.001, "l": 1, "tsp": 0.005, "tbsp": 0.015, "cup": 0.25, "floz": 0.0284130625, "pt": 0.56826125,
          "gal": 4.54609, "usfloz": 0.0295735295625, "uspt": 0.473176473, "usgal": 3.785411784}
SPEED = {"m/s": 1, "km/h": 1 / 3.6, "mph": 0.44704, "kn": 0.514444}
DATA = {"b": 1, "kb": 1e3, "mb": 1e6, "gb": 1e9, "tb": 1e12, "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "tib": 1024 ** 4}
AREA = {"m2": 1, "km2": 1e6, "ha": 1e4, "acre": 4046.8564224, "ft2": 0.09290304, "mi2": 2589988.110336}
SCALES = [LENGTH, MASS, VOLUME, SPEED, DATA, AREA]

ALIASES = {
    "millimetre": "mm", "millimeter": "mm", "centimetre": "cm", "centimeter": "cm", "metre": "m", "meter": "m",
    "kilometre": "km", "kilometer": "km", "inch": "in", "inches": "in", "foot": "ft", "feet": "ft", "yard": "yd",
    "mile": "mi", "miles": "mi", "milligram": "mg", "gram": "g", "kilogram": "kg", "kilo": "kg", "kilos": "kg",
    "tonne": "t", "ounce": "oz", "ounces": "oz", "pound": "lb", "pounds": "lb", "lbs": "lb", "stone": "st",
    "millilitre": "ml", "milliliter": "ml", "litre": "l", "liter": "l", "teaspoon": "tsp", "tablespoon": "tbsp",
    "fluid ounce": "floz", "fl oz": "floz", "pint": "pt", "pints": "pt", "gallon": "gal", "gallons": "gal",
    "us fluid ounce": "usfloz", "us fl oz": "usfloz", "us pint": "uspt", "us gallon": "usgal",
    "kph": "km/h", "kmh": "km/h", "kilometres per hour": "km/h", "miles per hour": "mph", "metres per second": "m/s",
    "knot": "kn", "knots": "kn", "byte": "b", "bytes": "b", "kilobyte": "kb", "megabyte": "mb", "gigabyte": "gb",
    "terabyte": "tb", "square metre": "m2", "sq m": "m2", "square kilometre": "km2", "hectare": "ha", "acres": "acre",
    "square foot": "ft2", "square feet": "ft2", "sq ft": "ft2", "square mile": "mi2",
    "celsius": "c", "centigrade": "c", "°c": "c", "fahrenheit": "f", "°f": "f", "kelvin": "k",
}
TEMPERATURES = {"c", "f", "k"}


def _unit(name: str) -> str:
    cleaned = " ".join(str(name or "").lower().replace("²", "2").split())
    return ALIASES.get(cleaned, ALIASES.get(cleaned.rstrip("s"), cleaned))


def _number(value: float) -> str:
    return f"{value:,.6g}" if abs(value) < 1e15 else f"{value:.6e}"


def _to_celsius(value: float, unit: str) -> float:
    return {"c": value, "f": (value - 32) * 5 / 9, "k": value - 273.15}[unit]


def _from_celsius(value: float, unit: str) -> float:
    return {"c": value, "f": value * 9 / 5 + 32, "k": value + 273.15}[unit]


def convert_units(value, from_unit, to_unit) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return f"'{value}' isn't a number."
    source, target = _unit(from_unit), _unit(to_unit)
    if source in TEMPERATURES and target in TEMPERATURES:
        result = _from_celsius(_to_celsius(amount, source), target)
        return f"{_number(amount)} {source.upper()} = {result:,.1f} {target.upper()}"
    for scale in SCALES:
        if source in scale and target in scale:
            result = amount * scale[source] / scale[target]
            return f"{_number(amount)} {source} = {_number(result)} {target}"
    return (f"Can't convert {from_unit} to {to_unit}: they aren't the same kind of unit, or one isn't known. "
            "Known: length, weight, volume (UK pints and gallons unless 'US'), speed, data, area and temperature.")


def _parse_date(text) -> date | None:
    cleaned = str(text or "").strip().lower()
    if cleaned in ("", "today", "now"):
        return date.today()
    if cleaned == "tomorrow":
        return date.today() + timedelta(days=1)
    if cleaned == "yesterday":
        return date.today() - timedelta(days=1)
    try:
        return date.fromisoformat(cleaned)
    except ValueError:
        return None


def _add_months(start: date, months: int) -> date:
    month_index = start.month - 1 + months
    year, month = start.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _describe(day: date) -> str:
    return f"{day:%A %d %B %Y}"


def date_maths(start=None, end=None, add_days=None, add_months=None) -> str:
    first = _parse_date(start)
    if first is None:
        return f"'{start}' isn't a date. Use YYYY-MM-DD, today, tomorrow or yesterday."
    lines = [f"Today is {_describe(date.today())}."]
    if first != date.today():
        lines.append(f"{first.isoformat()} is a {first:%A} ({_describe(first)}).")
    if end not in (None, ""):
        second = _parse_date(end)
        if second is None:
            return f"'{end}' isn't a date. Use YYYY-MM-DD, today, tomorrow or yesterday."
        days = (second - first).days
        weeks, remainder = divmod(abs(days), 7)
        direction = "after" if days >= 0 else "before"
        lines.append(f"{_describe(second)} is {_plural(abs(days), 'day')} {direction} {_describe(first)} "
                     f"({_plural(weeks, 'week')} and {_plural(remainder, 'day')}).")
    try:
        if add_days not in (None, ""):
            moved = first + timedelta(days=int(add_days))
            lines.append(f"{_describe(first)} {'+' if int(add_days) >= 0 else '-'} {abs(int(add_days))} days = {_describe(moved)}.")
        if add_months not in (None, ""):
            moved = _add_months(first, int(add_months))
            lines.append(f"{_describe(first)} {'+' if int(add_months) >= 0 else '-'} {abs(int(add_months))} months = {_describe(moved)}.")
    except (TypeError, ValueError, OverflowError):
        return "add_days and add_months must be whole numbers within the calendar's range."
    return "\n".join(lines)


def clock_maths(time_of_day, hours=0, minutes=0) -> str:
    try:
        start = datetime.strptime(str(time_of_day).strip(), "%H:%M")
        change = timedelta(hours=float(hours or 0), minutes=float(minutes or 0))
    except (TypeError, ValueError):
        return f"'{time_of_day}' isn't a 24-hour time like 19:50, or the hours/minutes aren't numbers."
    finish = start + change
    days_later = (finish.date() - start.date()).days
    suffix = "" if days_later == 0 else f" ({abs(days_later)} day{'s' if abs(days_later) != 1 else ''} {'later' if days_later > 0 else 'earlier'})"
    sign = "+" if change >= timedelta(0) else "-"
    total_minutes = int(abs(change).total_seconds() // 60)
    return f"{start:%H:%M} {sign} {total_minutes // 60}h {total_minutes % 60:02d}m = {finish:%H:%M}{suffix}"


def convert_currency(amount, from_currency, to_currency) -> str:
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return f"'{amount}' isn't a number."
    source, target = str(from_currency or "").strip().upper(), str(to_currency or "").strip().upper()
    if not (len(source) == 3 and len(target) == 3 and source.isalpha() and target.isalpha()):
        return "Use three-letter currency codes, e.g. USD, GBP, EUR."
    query = urllib.parse.urlencode({"amount": value, "from": source, "to": target})
    try:
        with urllib.request.urlopen(f"{CURRENCY_URL}?{query}", timeout=CURRENCY_TIMEOUT_SECONDS) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        return f"The exchange-rate service doesn't know {source} or {target} (HTTP {error.code})."
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        return f"Couldn't reach the exchange-rate service: {error}"
    rate = (data.get("rates") or {}).get(target)
    if rate is None:
        return f"No rate for {source} to {target}."
    return f"{value:,.2f} {source} = {rate:,.2f} {target} (European Central Bank rate for {data.get('date', 'today')})"


CURRENCY_NAMES = {
    "us dollars": "USD", "us dollar": "USD", "dollars": "USD", "dollar": "USD", "bucks": "USD", "$": "USD",
    "pounds sterling": "GBP", "pounds": "GBP", "pound": "GBP", "quid": "GBP", "sterling": "GBP", "£": "GBP",
    "euros": "EUR", "euro": "EUR", "€": "EUR", "yen": "JPY", "¥": "JPY", "canadian dollars": "CAD", "canadian dollar": "CAD",
    "australian dollars": "AUD", "australian dollar": "AUD", "swiss francs": "CHF", "swiss franc": "CHF",
    "yuan": "CNY", "rupees": "INR", "rupee": "INR", "zloty": "PLN", "kronor": "SEK", "krona": "SEK",
}
CURRENCY_WORD = "|".join(sorted((re.escape(name) for name in CURRENCY_NAMES), key=len, reverse=True)) + r"|[a-z]{3}"
CURRENCY_QUESTION = re.compile(
    rf"(?<![a-z])(?P<before>{CURRENCY_WORD})?\s*(?P<amount>\d[\d,]*(?:\.\d+)?)\s*(?P<after>{CURRENCY_WORD})?\s+(?:in|to|into)\s+(?P<target>{CURRENCY_WORD})\b",
    re.I)


def _currency_code(word) -> str | None:
    lowered = str(word or "").strip().lower()
    if lowered in CURRENCY_NAMES:
        return CURRENCY_NAMES[lowered]
    return lowered.upper() if re.fullmatch(r"[a-z]{3}", lowered) else None


def currency_facts(text) -> str:
    match = CURRENCY_QUESTION.search(str(text or ""))
    source = match and _currency_code(match.group("before") or match.group("after"))
    target = match and _currency_code(match.group("target"))
    if not (source and target):
        return "No amount and currencies found in the message; use convert_currency with three-letter codes."
    return convert_currency(match.group("amount").replace(",", ""), source, target)

def calculate(expression) -> str:
    text = quick.PERCENT_OF.sub(r"(\1/100*\2)", str(expression or "")).replace("^", "**").replace("×", "*").replace("÷", "/")
    text = text.replace(",", "")
    try:
        value = quick._evaluate(ast.parse(text, mode="eval"))
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError) as error:
        return f"Can't work that out ({error}). Use numbers with + - * / ** ^ and brackets, or 'N% of M'."
    shown = f"{value:,.10g}" if isinstance(value, float) and not value.is_integer() else f"{int(value):,}"
    return f"{expression} = {shown}"


NAMED_DAYS = {
    "christmas eve": (12, 24), "christmas day": (12, 25), "christmas": (12, 25), "xmas": (12, 25), "boxing day": (12, 26),
    "new year's eve": (12, 31), "new years eve": (12, 31), "new year's day": (1, 1), "new years day": (1, 1),
    "new year": (1, 1), "valentine's day": (2, 14), "valentines day": (2, 14), "valentine's": (2, 14),
    "st david's day": (3, 1), "st davids day": (3, 1), "st patrick's day": (3, 17), "halloween": (10, 31),
    "bonfire night": (11, 5), "guy fawkes night": (11, 5),
}
RELATIVE = re.compile(r"\b(?:in\s+)?(\d+)\s+(day|week|month)s?\s*(from (?:today|now)|after today|ago|time)?\b", re.I)


def _easter(year: int) -> date:
    golden = year % 19
    century, year_of_century = divmod(year, 100)
    leap_century, leap_rest = divmod(century, 4)
    moon_correction = (century + 8) // 25
    solar_correction = (century - moon_correction + 1) // 3
    epact = (19 * golden + century - leap_century - solar_correction + 15) % 30
    leap_year_of_century, rest_of_century = divmod(year_of_century, 4)
    weekday_offset = (32 + 2 * leap_rest + 2 * leap_year_of_century - epact - rest_of_century) % 7
    shift = (golden + 11 * epact + 22 * weekday_offset) // 451
    month, day = divmod(epact + weekday_offset - 7 * shift + 114, 31)
    return date(year, month, day + 1)


def _next(month: int, day: int, today: date) -> date:
    found = date(today.year, month, day)
    return found if found >= today else date(today.year + 1, month, day)


def _distance(day: date, today: date) -> str:
    days = (day - today).days
    if days == 0:
        return "today"
    weeks, remainder = divmod(abs(days), 7)
    span = _plural(abs(days), "day") + (f" ({_plural(weeks, 'week')} and {_plural(remainder, 'day')})" if weeks else "")
    return f"{span} from today" if days > 0 else f"{span} ago"


def date_facts(text) -> str:
    today = date.today()
    lowered = str(text or "").lower().replace("\u2019", "'")
    found = []
    for name in sorted(NAMED_DAYS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", lowered) and not any(name in earlier for earlier, _ in found):
            found.append((name, _next(*NAMED_DAYS[name], today)))
    if re.search(r"\beaster\b", lowered):
        easter = _easter(today.year)
        found.append(("easter sunday", easter if easter >= today else _easter(today.year + 1)))
    try:
        written = calendar_feed.written_date(str(text or ""), today)
    except ValueError:
        written = None
    if written is not None:
        found.append(("the date", written))
    for amount, unit, direction in RELATIVE.findall(lowered):
        count = int(amount) * (-1 if (direction or "").lower() == "ago" else 1)
        moved = _add_months(today, count) if unit.lower() == "month" else today + timedelta(days=count * (7 if unit.lower() == "week" else 1))
        found.append((f"{amount} {unit}s {'ago' if count < 0 else 'from today'}", moved))
    lines = [f"Today is {_describe(today)}."]
    lines += [f"{label[0].upper() + label[1:]}: {_describe(day)}, {_distance(day, today)}." for label, day in found]
    if not found:
        lines.append("No dates found in the message; use date_maths with the dates.")
    return "\n".join(lines)


PLACE_AREAS = {"Africa", "America", "Asia", "Atlantic", "Australia", "Europe", "Indian", "Pacific"}
WORDS_THAT_ARE_ZONES = {"easter", "wake", "midway", "center"}
PLACE_ALIASES = {
    "japan": "Asia/Tokyo", "china": "Asia/Shanghai", "beijing": "Asia/Shanghai", "india": "Asia/Kolkata",
    "delhi": "Asia/Kolkata", "new delhi": "Asia/Kolkata", "mumbai": "Asia/Kolkata", "bangalore": "Asia/Kolkata",
    "korea": "Asia/Seoul", "south korea": "Asia/Seoul", "thailand": "Asia/Bangkok", "vietnam": "Asia/Ho_Chi_Minh",
    "philippines": "Asia/Manila", "indonesia": "Asia/Jakarta", "bali": "Asia/Makassar", "uae": "Asia/Dubai",
    "abu dhabi": "Asia/Dubai", "uk": "Europe/London", "england": "Europe/London", "britain": "Europe/London",
    "scotland": "Europe/London", "wales": "Europe/London", "ireland": "Europe/Dublin", "france": "Europe/Paris",
    "germany": "Europe/Berlin", "spain": "Europe/Madrid", "italy": "Europe/Rome", "netherlands": "Europe/Amsterdam",
    "poland": "Europe/Warsaw", "greece": "Europe/Athens", "turkey": "Europe/Istanbul", "russia": "Europe/Moscow",
    "australia": "Australia/Sydney", "new zealand": "Pacific/Auckland", "canada": "America/Toronto",
    "brazil": "America/Sao_Paulo", "mexico": "America/Mexico_City", "egypt": "Africa/Cairo",
    "south africa": "Africa/Johannesburg", "cape town": "Africa/Johannesburg", "hawaii": "Pacific/Honolulu",
    "california": "America/Los_Angeles", "san francisco": "America/Los_Angeles", "seattle": "America/Los_Angeles",
    "las vegas": "America/Los_Angeles", "texas": "America/Chicago", "dallas": "America/Chicago",
    "houston": "America/Chicago", "washington": "America/New_York", "boston": "America/New_York",
    "miami": "America/New_York", "florida": "America/New_York",
}
FALLBACK_HOME_ZONE = "Europe/London"
MAX_PLACES = 4
CLOCK_FORMAT = "%H:%M on %A %d %B %Y"


def now_in(zone: ZoneInfo) -> datetime:
    return datetime.now(zone)


def _place_zones() -> dict[str, str]:
    zones = {}
    for zone in available_timezones():
        area, _, city = zone.partition("/")
        if area not in PLACE_AREAS or not city or "/" in city:
            continue
        name = city.replace("_", " ").lower()
        if name not in WORDS_THAT_ARE_ZONES:
            zones.setdefault(name, zone)
    zones.update(PLACE_ALIASES)
    return zones


def _home_zone(home) -> ZoneInfo:
    try:
        return ZoneInfo(str(home or FALLBACK_HOME_ZONE))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(FALLBACK_HOME_ZONE)


def _gap_from_home(minutes: int) -> str:
    if minutes == 0:
        return "the same time as home"
    hours, extra = divmod(abs(minutes), 60)
    size = _plural(hours, "hour") if hours else ""
    if extra:
        size = f"{size} {_plural(extra, 'minute')}".strip()
    return f"{size} {'ahead of' if minutes > 0 else 'behind'} home"


def _places_named(text) -> list[tuple[str, str]]:
    lowered = str(text or "").lower().replace("\u2019", "'")
    zones = _place_zones()
    found = []
    for place in sorted(zones, key=len, reverse=True):
        if re.search(rf"\b{re.escape(place)}\b", lowered) and not any(place in earlier for earlier, _ in found):
            found.append((place, zones[place]))
    return found[:MAX_PLACES]


def place_time(text, home=FALLBACK_HOME_ZONE) -> str:
    home_zone = _home_zone(home)
    home_now = now_in(home_zone)
    lines = [f"At home ({home_zone.key}) it is {home_now:{CLOCK_FORMAT}}."]
    places = _places_named(text)
    for place, zone in places:
        there = home_now.astimezone(ZoneInfo(zone))
        minutes = round((there.utcoffset() - home_now.utcoffset()).total_seconds() / 60)
        lines.append(f"In {place.title()} ({zone}) it is {there:{CLOCK_FORMAT}}, {_gap_from_home(minutes)}.")
    if not places:
        lines.append("No place found in the message; use the time tool's convert_time.")
    return "\n".join(lines)


def _tool(name, description, properties, required):
    return {"type": "function", "function": {"name": name, "description": description,
                                             "parameters": {"type": "object", "properties": properties, "required": required}}}


TOOLS = {
    "currency_facts": (lambda a: currency_facts(a.get("text")), _tool(
        "currency_facts", "Convert an amount of money written in words, e.g. '50 dollars in pounds', at today's rate.",
        {"text": {"type": "string", "description": "the amount and currencies, in words"}}, ["text"])),
    "calculate": (lambda a: calculate(a.get("expression")), _tool(
        "calculate", "Work out arithmetic exactly instead of in your head: + - * / ** ^ brackets and 'N% of M'.",
        {"expression": {"type": "string", "description": "e.g. 17.5% of 249.99, or (42*3)/7"}}, ["expression"])),
    "date_maths": (lambda a: date_maths(a.get("start"), a.get("end"), a.get("add_days"), a.get("add_months")), _tool(
        "date_maths", "Exact date sums, never search the web for these. Weekday of a date: start=the date. "
                      "Days until or between dates: start=today (or the first date), end=the other date. "
                      "A date plus or minus time: start and add_days or add_months. Dates as YYYY-MM-DD, or today/tomorrow/yesterday.",
        {"start": {"type": "string", "description": "the date to start from (default today)"},
         "end": {"type": "string", "description": "optional second date, to count the days between"},
         "add_days": {"type": "integer", "description": "optional days to add (negative to go back)"},
         "add_months": {"type": "integer", "description": "optional months to add (negative to go back)"}}, [])),
    "date_facts": (lambda a: date_facts(a.get("text")), _tool(
        "date_facts", "Every date in a piece of text worked out: Christmas, Easter, '14 Feb 2027', 'in 3 weeks', '90 days from today', "
                      "with its weekday and how many days away it is.",
        {"text": {"type": "string", "description": "the words containing the dates, e.g. the user's question"}}, ["text"])),
    "place_time": (lambda a: place_time(a.get("text"), a.get("home")), _tool(
        "place_time", "The time right now in the places named in a piece of text (cities or countries), and how far ahead or behind home they are.",
        {"text": {"type": "string", "description": "the words naming the places, e.g. the user's question"},
         "home": {"type": "string", "description": "home time zone, e.g. Europe/London"}}, ["text"])),
    "clock_maths": (lambda a: clock_maths(a.get("time"), a.get("hours", 0), a.get("minutes", 0)), _tool(
        "clock_maths", "Add or take away hours and minutes from a time of day, e.g. when a film ending or a journey arrives.",
        {"time": {"type": "string", "description": "24-hour time, e.g. 19:50"},
         "hours": {"type": "number", "description": "hours to add (negative to take away)"},
         "minutes": {"type": "number", "description": "minutes to add (negative to take away)"}}, ["time"])),
    "convert_units": (lambda a: convert_units(a.get("value"), a.get("from_unit"), a.get("to_unit")), _tool(
        "convert_units", "Convert a measurement: length, weight, volume, speed, data size, area or temperature.",
        {"value": {"type": "number"},
         "from_unit": {"type": "string", "description": "e.g. fahrenheit, miles, kg, pints, mph, GB"},
         "to_unit": {"type": "string", "description": "e.g. celsius, km, lb, litres, km/h, MiB"}}, ["value", "from_unit", "to_unit"])),
    "convert_currency": (lambda a: convert_currency(a.get("amount"), a.get("from_currency"), a.get("to_currency")), _tool(
        "convert_currency", "Convert money between currencies at today's exchange rate.",
        {"amount": {"type": "number"},
         "from_currency": {"type": "string", "description": "three-letter code, e.g. USD"},
         "to_currency": {"type": "string", "description": "three-letter code, e.g. GBP"}}, ["amount", "from_currency", "to_currency"])),
}

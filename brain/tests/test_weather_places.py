import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-weather-places-")
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("STYLE_FILE", "style.json"),
                  ("EXPIRY_STATE", "expiry.json"), ("CALENDAR_REMINDED", "calendar-reminded.json"),
                  ("EMAIL_WATCH_STATE", "email-watch.json"), ("DESKTOP_LOOK_STATE", "desktop-look.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import brain  # noqa: E402
import connections  # noqa: E402
import weather  # noqa: E402

DAY = date.today()
HOME = {"place": "Ebbw Vale", "latitude": 51.778, "longitude": -3.207}
EDINBURGH = {"name": "Edinburgh", "country": "United Kingdom", "latitude": 55.95, "longitude": -3.19, "timezone": "Europe/London"}


LEEDS = {"name": "Leeds", "country": "United Kingdom", "latitude": 53.8, "longitude": -1.55, "timezone": "Europe/London"}
NEW_YORK = {"name": "New York", "country": "United States", "latitude": 40.71, "longitude": -74.01, "timezone": "America/New_York"}
PARIS = {"name": "Paris", "country": "France", "latitude": 48.85, "longitude": 2.35, "timezone": "Europe/Paris"}
WEEK = [DAY + timedelta(days=ahead) for ahead in range(weather.MAX_DAYS)]


class FakeOpenMeteo:
    def __init__(self, places=(EDINBURGH,), days=2):
        self.places = list(places)
        self.days = days
        self.forecasts = []
        self.searches = []

    def __call__(self, url):
        if url.startswith(weather.GEOCODE_URL):
            self.searches.append(url)
            return {"results": self.places}
        self.forecasts.append(url)
        if self.days == 2:
            return {"current": {"temperature_2m": 4, "weather_code": 0, "wind_speed_10m": 5},
                    "daily": {"time": [DAY.isoformat(), (DAY + timedelta(days=1)).isoformat()], "weather_code": [3, 61],
                              "temperature_2m_min": [1, 2], "temperature_2m_max": [6, 8], "precipitation_probability_max": [10, 70],
                              "wind_speed_10m_max": [9, 12]}}
        week = WEEK[:self.days]
        return {"current": {"temperature_2m": 4, "weather_code": 0, "wind_speed_10m": 5},
                "daily": {"time": [day.isoformat() for day in week], "weather_code": [3] * len(week),
                          "temperature_2m_min": [1] * len(week), "temperature_2m_max": [6] * len(week),
                          "precipitation_probability_max": [10] * len(week), "wind_speed_10m_max": [9] * len(week)}}


def searched_for(fake: FakeOpenMeteo) -> list[str]:
    return [parse_qs(urlparse(url).query)["name"][0] for url in fake.searches]


def label_for(day: date) -> str:
    return "Today" if day == DAY else "Tomorrow" if day == DAY + timedelta(days=1) else day.strftime("%A")


class WeatherForPlaces(unittest.TestCase):
    def ask(self, question, places=(EDINBURGH,)):
        fake = FakeOpenMeteo(places)
        with mock.patch.object(weather, "get_json", side_effect=fake):
            return weather.answer(question, HOME), fake

    def test_a_forecast_for_somewhere_else_is_for_that_place(self):
        reply, fake = self.ask("what's the weather in Edinburgh today?")
        self.assertIn("Edinburgh", reply)
        self.assertNotIn("Ebbw Vale", reply)
        self.assertIn("latitude=55.95", fake.forecasts[0])

    def test_home_is_still_home(self):
        for question in ("will it rain tomorrow?", "what's the weather in Ebbw Vale?", "is it going to rain in the morning?",
                         "will it rain at 3pm?", "what's the forecast for tomorrow?", "is there rain coming today?",
                         "will it snow at Christmas?", "should I take an umbrella for work today?"):
            reply, fake = self.ask(question)
            self.assertIn("Ebbw Vale", reply, question)
            self.assertEqual(fake.searches, [], question)

    def test_climate_and_there_questions_are_not_answered_with_a_forecast(self):
        for question in ("what's the weather usually like there then?", "what's the weather like in Edinburgh in March?",
                         "is it usually rainy in Spain?", "what's the climate like in Iceland?", "is it raining there?"):
            reply, _ = self.ask(question)
            self.assertIsNone(reply, question)
            self.assertTrue(weather.not_a_home_forecast(question, HOME), question)

    def test_a_place_that_cannot_be_found_gets_no_answer(self):
        reply, _ = self.ask("what's the weather in Narnia?", places=())
        self.assertIsNone(reply)

    def test_home_forecast_questions_are_home_forecasts(self):
        for question in ("will it rain tomorrow?", "is there rain coming today?", "do I need a coat?"):
            self.assertFalse(weather.not_a_home_forecast(question, HOME), question)

    def test_the_weather_tool_takes_a_place(self):
        fake = FakeOpenMeteo()
        with mock.patch.object(weather, "get_json", side_effect=fake):
            result, _ = brain.run_native("weather", {"days": 1, "place": "Edinburgh"}, {"weather": HOME}, False, None)
        self.assertIn("Weather for Edinburgh", result)
        with mock.patch.object(weather, "get_json", side_effect=fake):
            result, _ = brain.run_native("weather", {"place": "the Lake District"}, {"weather": HOME}, False, None)
        self.assertIn("Weather for Edinburgh", result)
        self.assertIn("place", brain.NATIVE_TOOLS["weather"]["function"]["parameters"]["properties"])

    def test_the_weather_tool_says_when_a_place_is_not_found(self):
        with mock.patch.object(weather, "get_json", side_effect=FakeOpenMeteo(places=())):
            result, _ = brain.run_native("weather", {"place": "Narnia"}, {"weather": HOME}, False, None)
        self.assertIn("Couldn't find a place called Narnia", result)

    def test_the_home_lookup_is_skipped_for_other_places_and_climate(self):
        config = {"weather": HOME, "lookups": [{"tool": "weather", "pattern": "weather", "arguments": {"days": 2}}]}
        with mock.patch.object(weather, "get_json", side_effect=FakeOpenMeteo()):
            used, _, _ = brain.look_up_first(config, "what's the weather usually like there then?")
            self.assertEqual(used, [])
            used, _, _ = brain.look_up_first(config, "what's the weather today?")
            self.assertEqual(used, ["weather"])


class DaysTimesAndPeopleAreNotPlaces(unittest.TestCase):
    HOME_FORECASTS = ("what's the forecast for Saturday", "is it going to rain for us tomorrow?", "weather for Friday",
                      "is it going to rain at lunchtime?", "what's the weather for Sunday morning?", "will it rain for me today?",
                      "weather for the 14th", "what's the forecast for 3pm?", "is it going to be sunny for them at teatime?",
                      "forecast for november", "weather for Tues", "will it be windy at dinnertime?", "rain for everyone tomorrow?",
                      "what's the weather looking like for Wednesday afternoon?")

    def ask(self, question, days=weather.MAX_DAYS):
        fake = FakeOpenMeteo(places=(), days=days)
        with mock.patch.object(weather, "get_json", side_effect=fake):
            return weather.answer(question, HOME), fake

    def test_none_of_them_is_looked_up_as_a_town(self):
        for question in self.HOME_FORECASTS:
            self.assertIsNone(weather.named_place(question), question)
            self.assertFalse(weather.not_a_home_forecast(question, HOME), question)

    def test_they_get_the_home_forecast(self):
        for question in self.HOME_FORECASTS:
            reply, fake = self.ask(question)
            self.assertIsNotNone(reply, question)
            self.assertIn("in Ebbw Vale:", reply, question)
            self.assertEqual(fake.searches, [], question)

    def test_a_named_day_gets_that_days_forecast(self):
        for day in WEEK:
            reply, _ = self.ask(f"what's the forecast for {day:%A}")
            self.assertTrue(reply.startswith(label_for(day).split(" (")[0]), (day, reply))

    def test_rain_for_us_tomorrow_is_tomorrow_at_home(self):
        reply, _ = self.ask("is it going to rain for us tomorrow?")
        self.assertTrue(reply.startswith("Tomorrow"), reply)

    def test_the_quick_path_answers_for_home(self):
        fake = FakeOpenMeteo(places=(), days=weather.MAX_DAYS)
        with mock.patch.object(weather, "get_json", side_effect=fake), \
                mock.patch.object(connections, "load_config", return_value={"weather": HOME}):
            reply, tool = brain.quick_action("what's the forecast for Saturday")
        self.assertEqual(tool, "weather")
        self.assertIn("Ebbw Vale", reply)
        self.assertEqual(fake.searches, [])

    def test_the_home_lookup_runs_for_a_day_or_a_time(self):
        config = {"weather": HOME, "lookups": [{"tool": "weather", "pattern": "weather|forecast|rain", "arguments": {"days": 7}}]}
        for question in ("weather for Friday", "will it rain at lunchtime?", "is it going to rain for us tomorrow?"):
            with mock.patch.object(weather, "get_json", side_effect=FakeOpenMeteo(places=(), days=weather.MAX_DAYS)):
                used, _, _ = brain.look_up_first(config, question)
            self.assertEqual(used, ["weather"], question)


class RealPlacesStillWork(unittest.TestCase):
    def ask(self, question, place):
        fake = FakeOpenMeteo(places=(place,), days=weather.MAX_DAYS)
        with mock.patch.object(weather, "get_json", side_effect=fake):
            return weather.answer(question, HOME), fake

    def test_a_town_a_city_of_two_words_and_a_capital(self):
        for question, place, label in (("weather in Leeds", LEEDS, "Today"),
                                       ("forecast for New York on Saturday", NEW_YORK, label_for(next(d for d in WEEK if d.weekday() == 5))),
                                       ("rain in Paris tomorrow", PARIS, "Tomorrow"),
                                       ("weather in leeds", LEEDS, "Today")):
            reply, fake = self.ask(question, place)
            self.assertEqual(searched_for(fake), [place["name"] if question != "weather in leeds" else "leeds"], question)
            self.assertIn(f"in {place['name']}, {place['country']}:", reply, question)
            self.assertTrue(reply.startswith(label.split(" (")[0]), (question, reply))

    def test_a_place_after_a_day_or_a_person_is_still_found(self):
        for question in ("weather for Saturday in Leeds", "is it going to rain for us in Leeds tomorrow?",
                         "what's the forecast at lunchtime in Leeds?"):
            reply, fake = self.ask(question, LEEDS)
            self.assertEqual(searched_for(fake), ["Leeds"], question)
            self.assertIn("Leeds, United Kingdom", reply, question)

    def test_a_place_ends_before_a_day_or_a_time(self):
        for question in ("weather in Leeds Saturday", "rain in Leeds this afternoon", "weather in Leeds at lunchtime"):
            self.assertEqual(weather.named_place(question), "Leeds", question)


if __name__ == "__main__":
    unittest.main()

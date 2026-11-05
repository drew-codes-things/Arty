import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import reckon


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 29)


class Dates(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(reckon, "date", FixedDate)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_days_until(self):
        self.assertIn("57 days after", reckon.date_maths("today", "2026-12-25"))

    def test_weekday(self):
        self.assertIn("2027-02-14 is a Sunday", reckon.date_maths("2027-02-14"))

    def test_days_before(self):
        self.assertIn("3 days before", reckon.date_maths("2026-10-29", "2026-10-26"))

    def test_add_months_clamps_to_month_end(self):
        self.assertIn("Saturday 28 February 2026", reckon.date_maths("2026-01-31", add_months=1))

    def test_add_negative_days(self):
        self.assertIn("Monday 19 October 2026", reckon.date_maths("today", add_days=-10))

    def test_bad_input(self):
        self.assertIn("isn't a date", reckon.date_maths("next week"))
        self.assertIn("whole numbers", reckon.date_maths("today", add_days="lots"))


class DateFacts(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(reckon, "date", FixedDate)
        patcher.start()
        self.addCleanup(patcher.stop)
        written = mock.patch.object(reckon.calendar_feed, "written_date", side_effect=self.written)
        written.start()
        self.addCleanup(written.stop)

    @staticmethod
    def written(text, today):
        return date(2027, 2, 14) if "14 February 2027" in text else None

    def test_named_days(self):
        facts = reckon.date_facts("How many days until Christmas?")
        self.assertIn("Christmas: Friday 25 December 2026, 57 days (8 weeks and 1 day) from today.", facts)
        self.assertNotIn("Christmas eve", facts)

    def test_written_date(self):
        self.assertIn("Sunday 14 February 2027", reckon.date_facts("What day of the week is 14 February 2027?"))

    def test_relative(self):
        self.assertIn("Wednesday 27 January 2027", reckon.date_facts("What date is 90 days from today?"))
        self.assertIn("3 weeks ago: Thursday 08 October 2026, 21 days (3 weeks and 0 days) ago.", reckon.date_facts("the date 3 weeks ago"))

    def test_easter(self):
        self.assertIn("Sunday 28 March 2027", reckon.date_facts("when is easter?"))
        self.assertEqual(reckon._easter(2024), date(2024, 3, 31))
        self.assertEqual(reckon._easter(2026), date(2026, 4, 5))

    def test_nothing_found(self):
        self.assertIn("No dates found", reckon.date_facts("what is 2+2"))


class Clock(unittest.TestCase):
    def test_film_end(self):
        self.assertEqual(reckon.clock_maths("19:50", 2, 47), "19:50 + 2h 47m = 22:37")

    def test_past_midnight(self):
        self.assertEqual(reckon.clock_maths("23:30", minutes=45), "23:30 + 0h 45m = 00:15 (1 day later)")

    def test_going_back(self):
        self.assertEqual(reckon.clock_maths("01:00", hours=-2), "01:00 - 2h 00m = 23:00 (1 day earlier)")

    def test_bad_time(self):
        self.assertIn("isn't a 24-hour time", reckon.clock_maths("7pm", 1))


class Units(unittest.TestCase):
    def test_temperature(self):
        self.assertEqual(reckon.convert_units(72, "fahrenheit", "celsius"), "72 F = 22.2 C")
        self.assertEqual(reckon.convert_units(0, "°C", "kelvin"), "0 C = 273.1 K")

    def test_length_and_weight(self):
        self.assertEqual(reckon.convert_units(10, "miles", "km"), "10 mi = 16.0934 km")
        self.assertEqual(reckon.convert_units(1, "stone", "kg"), "1 st = 6.35029 kg")

    def test_uk_and_us_pints(self):
        self.assertEqual(reckon.convert_units(1, "pint", "ml"), "1 pt = 568.261 ml")
        self.assertEqual(reckon.convert_units(1, "US pint", "ml"), "1 uspt = 473.176 ml")

    def test_data(self):
        self.assertEqual(reckon.convert_units(1, "GiB", "MB"), "1 gib = 1,073.74 mb")

    def test_mismatched(self):
        self.assertIn("Can't convert", reckon.convert_units(5, "kg", "miles"))
        self.assertIn("isn't a number", reckon.convert_units("five", "kg", "lb"))


class Calculate(unittest.TestCase):
    def test_percent_and_powers(self):
        self.assertEqual(reckon.calculate("17.5% of 249.99"), "17.5% of 249.99 = 43.74825")
        self.assertEqual(reckon.calculate("2^20"), "2^20 = 1,048,576")
        self.assertEqual(reckon.calculate("1,200 / 4"), "1,200 / 4 = 300")

    def test_rejects_code(self):
        self.assertIn("Can't work that out", reckon.calculate("__import__('os')"))
        self.assertIn("Can't work that out", reckon.calculate("1/0"))


class Currency(unittest.TestCase):
    def test_bad_codes(self):
        self.assertIn("three-letter", reckon.convert_currency(5, "dollars", "GBP"))
        self.assertIn("isn't a number", reckon.convert_currency("lots", "USD", "GBP"))

    def test_rate(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        with mock.patch.object(reckon.urllib.request, "urlopen", return_value=response), \
                mock.patch.object(reckon.json, "load", return_value={"date": "2026-10-29", "rates": {"GBP": 37.546}}):
            self.assertEqual(reckon.convert_currency(50, "usd", "gbp"),
                             "50.00 USD = 37.55 GBP (European Central Bank rate for 2026-10-29)")

    def test_unreachable(self):
        with mock.patch.object(reckon.urllib.request, "urlopen", side_effect=reckon.urllib.error.URLError("down")):
            self.assertIn("Couldn't reach", reckon.convert_currency(1, "USD", "GBP"))


class CurrencyFacts(unittest.TestCase):
    def converted(self, text):
        with mock.patch.object(reckon, "convert_currency", side_effect=lambda *args: args):
            return reckon.currency_facts(text)

    def test_words(self):
        self.assertEqual(self.converted("How much is 50 US dollars in pounds right now?"), ("50", "USD", "GBP"))
        self.assertEqual(self.converted("£1,200 to euros"), ("1200", "GBP", "EUR"))
        self.assertEqual(self.converted("convert 30 eur into usd"), ("30", "EUR", "USD"))

    def test_nothing_found(self):
        self.assertIn("No amount", reckon.currency_facts("is the pound strong?"))

if __name__ == "__main__":
    unittest.main()

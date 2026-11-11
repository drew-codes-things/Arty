import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("BRAIN_DB", os.path.join(tempfile.mkdtemp(prefix="arty-phrases-"), "arty.db"))

import personal  # noqa: E402
import phone  # noqa: E402
import quick  # noqa: E402
import research  # noqa: E402
import safety  # noqa: E402
import store  # noqa: E402


class PhoneRequests(unittest.TestCase):
    def test_things_that_only_start_with_call_or_text_are_not_calls(self):
        for text in ["phone battery percentage", "call the API and show me the result", "message of the day",
                     "text size too small", "ring doorbell battery level", "phone settings", "call the function twice"]:
            self.assertIsNone(phone.from_request(text), text)

    def test_real_calls_and_texts_still_work(self):
        self.assertEqual(phone.from_request("call the dentist")["who"], "Dentist")
        self.assertEqual(phone.from_request("ring mum and dad")["who"], "Mum and dad")
        self.assertEqual(phone.from_request("text Sam saying the API is down")["body"], "The API is down")


class WebQuestions(unittest.TestCase):
    def test_follow_ups_and_questions_about_arty_or_the_user_stay_in_the_conversation(self):
        for text in ["which of those is best?", "what are you currently running on?", "I'm currently learning Rust, any tips",
                     "what's the best way to refactor this:\ndef f(x):\n    return x", "is that one the latest?",
                     "which one do you recommend from the list above"]:
            self.assertFalse(research.is_quick_question(text), text)

    def test_real_web_questions_still_go_to_the_web(self):
        for text in ["who won the last football world cup?", "what's the best way to learn Python", "how do I make pancakes",
                     "can you recommend a good budget laptop"]:
            self.assertTrue(research.is_quick_question(text), text)


class ServerAddresses(unittest.TestCase):
    def test_other_ip_questions_are_not_about_the_servers(self):
        for text in ["what's my public IP?", "how do I find a website's IP", "what is an IP address", "what's my router's ip"]:
            self.assertFalse(quick.asks_for_ips(text), text)

    def test_server_ip_questions_still_are(self):
        for text in ["what are all the IPs of my servers?", "what's the IP address of the minecraft server", "list the guest IPs"]:
            self.assertTrue(quick.asks_for_ips(text), text)


class CantDo(unittest.TestCase):
    def test_ideas_and_sending_things_are_not_orders_or_payments(self):
        for text in ["get me a recipe for dinner", "get me lunch ideas", "send 5 photos to my phone", "send 2 emails to Sam",
                     "pay 3 visits to the gym this week"]:
            self.assertIsNone(quick.cant_do(text), text)

    def test_payments_are_still_refused(self):
        for text in ["send £20 to Jake", "transfer 50 pounds to my savings", "pay $15 to Sam", "order me a pizza"]:
            self.assertIn("can't", quick.cant_do(text) or "", text)


class Sums(unittest.TestCase):
    def test_dates_and_ranges_are_not_sums(self):
        for text in ["24/7", "10-12", "2026-11-11", "1/2", "open 9-5"]:
            self.assertIsNone(quick.calculate(text), text)

    def test_asked_sums_still_work(self):
        self.assertEqual(quick.calculate("what's 10-12"), "10-12 = -2")
        self.assertEqual(quick.calculate("calculate 24/8"), "24/8 = 3")
        self.assertEqual(quick.calculate("12 * 3"), "12 * 3 = 36")


class SelfHarm(unittest.TestCase):
    def test_facts_and_accidents_get_an_answer(self):
        for text in ["what's the suicide rate in the UK", "I cut myself shaving", "suicide prevention charities near me",
                     "I cut myself cooking last night, how do I clean it"]:
            self.assertIsNone(safety.check(text), text)

    def test_the_wording_that_slipped_through_after_the_narrowing_gets_care(self):
        for text in ["I cut myself on purpose", "the suicidal thoughts are back", "I am going to commit suicide in the uk",
                     "I cut myself while I was alone", "suicide in history class made me think about doing it",
                     "I cut myself with a knife when nobody was home"]:
            self.assertEqual((safety.check(text) or ("",))[0], "self_harm", text)

    def test_accidents_with_while_are_still_answered(self):
        for text in ["I cut myself while shaving", "I cut myself while chopping onions"]:
            self.assertIsNone(safety.check(text), text)

    def test_cries_for_help_still_get_care(self):
        for text in ["i want to kill myself", "I've been thinking about suicide", "I cut myself again", "i feel suicidal"]:
            self.assertEqual((safety.check(text) or ("",))[0], "self_harm", text)


class ListItems(unittest.TestCase):
    def setUp(self):
        store.init()
        personal.init()
        with store.write_lock, store.connect() as connection:
            connection.execute("DELETE FROM list_items")

    def items(self):
        with store.connect() as connection:
            return [row["item"] for row in connection.execute("SELECT item FROM list_items ORDER BY id")]

    def test_one_thing_with_and_in_its_name_stays_one_item(self):
        personal.list_add("shopping", "salt and vinegar crisps, mac and cheese and fish and chips")
        self.assertEqual(self.items(), ["salt and vinegar crisps", "mac and cheese", "fish and chips"])

    def test_separate_things_are_still_split(self):
        personal.list_add("shopping", "milk and eggs, bread and butter and jam")
        self.assertEqual(self.items(), ["milk", "eggs", "bread and butter", "jam"])


class BadNumbers(unittest.TestCase):
    def setUp(self):
        store.init()
        personal.init()

    def test_a_word_where_a_number_belongs_is_a_plain_answer(self):
        self.assertIn("no note", personal.note_delete("abc"))
        self.assertIn("no deadline", personal.deadline_done("the essay"))
        self.assertIn("no card", personal.quiz_mark("x", True))


if __name__ == "__main__":
    unittest.main()

"""Quick actions: which everyday requests code handles directly, and what it takes from them.
Run from brain/: python3 -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import quick  # noqa: E402


class Calendar(unittest.TestCase):
    def test_plain_requests(self):
        self.assertEqual(quick.calendar_add("put the dentist in my calendar next Tuesday at 3pm"), ("the dentist", "next Tuesday at 3pm"))
        self.assertEqual(quick.calendar_add("Can you add football to my diary for Saturday 10am?"), ("football", "Saturday 10am"))
        self.assertEqual(quick.calendar_add("book a haircut in the calendar on 25 Dec 14:00"), ("a haircut", "25 Dec 14:00"))

    def test_left_to_the_model(self):
        self.assertIsNone(quick.calendar_add("put the dentist in my calendar"))  # no when
        self.assertIsNone(quick.calendar_add("what's in my calendar tomorrow?"))
        self.assertIsNone(quick.calendar_add("put the dentist in my calendar on Tuesday and then remind me the day before"))


class Sums(unittest.TestCase):
    def test_worked_out_exactly(self):
        self.assertEqual(quick.calculate("what's 12 times 15?"), "12 times 15 = 180")
        self.assertEqual(quick.calculate("what is 15% of 80"), "15% of 80 = 12")
        self.assertEqual(quick.calculate("7.5 divided by 3"), "7.5 divided by 3 = 2.5")
        self.assertEqual(quick.calculate("1,250 + 3 x 4"), "1,250 + 3 x 4 = 1,262")
        self.assertEqual(quick.calculate("what is 2 to the power of 10"), "2 to the power of 10 = 1,024")

    def test_not_sums(self):
        for text in ["who won the 2026 world cup", "what's the best way to learn python", "remind me at 6", "what is 42",
                     "is CT 104 running", "1 divided by 0", "9 to the power of 99999"]:
            self.assertIsNone(quick.calculate(text), text)


class Addresses(unittest.TestCase):
    STATUS = """Host servmox (192.168.1.253): online, CPU 3%
CT 100 core-sys (192.168.1.10): running, CPU 7%
VM 106 test-debian: stopped
Storage local: 20.1G used of 37.6G"""

    def test_listed_from_proxmox_status(self):
        self.assertEqual(quick.ip_list(self.STATUS), "- Host servmox: 192.168.1.253\n- CT 100 core-sys: 192.168.1.10")
        self.assertTrue(quick.asks_for_ips("what are all the IPs of my servers?"))
        self.assertFalse(quick.asks_for_ips("is my zip file ready"))


class Lists(unittest.TestCase):
    def test_plain_requests(self):
        self.assertEqual(quick.list_add("add milk and eggs to my shopping list"), ("milk and eggs", "shopping"))
        self.assertEqual(quick.list_add("please put call mum on the to-do list."), ("call mum", "to-do"))

    def test_left_to_the_model(self):
        self.assertIsNone(quick.list_add("what's on my shopping list?"))
        self.assertIsNone(quick.list_add("add milk to my shopping list and then email it to me"))



class CantDo(unittest.TestCase):
    def test_things_arty_has_no_way_to_do_get_a_plain_no(self):
        for text in ["turn on my living room lights", "switch the heating off",
                     "please turn off the kettle", "order me a pizza from dominos", "get us a takeaway", "send £20 to Jake",
                     "book me a table at nandos for 7pm", "can you book a haircut for Saturday"]:
            self.assertIn("can't", quick.cant_do(text) or "", text)

    def test_things_arty_can_do_or_questions_are_left_alone(self):
        for text in ["call me Drew from now on", "what's the best pizza in Cardiff", "how do I turn off my boiler",
                     "turn up the volume", "remind me to call my mum at 5pm", "who called it the Big Apple"]:
            self.assertIsNone(quick.cant_do(text), text)


if __name__ == "__main__":
    unittest.main()

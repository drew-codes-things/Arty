import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import e2e_battery


class FakeBrain(e2e_battery.Brain):
    def __init__(self, schedules, events):
        super().__init__("http://brain.invalid/v1", "unused")
        self.schedules, self.events = schedules, events
        self.deleted, self.posted = [], []

    def get(self, path):
        if path == "/schedules":
            return {"schedules": [{"id": i, "title": t} for i, t in self.schedules.items()]}
        return {"events": [{"id": i, "uid": f"own-{i}", "title": t} for i, t in self.events.items()]
                + [{"id": "g1", "uid": "google-g1", "title": "TESTBATTERY google"}], "problems": []}

    def delete(self, path):
        self.deleted.append(path)

    def post(self, path, body, extra_headers=None):
        self.posted.append((path, body))
        return {}


class Cleanup(unittest.TestCase):
    def test_only_new_marked_things_are_removed_and_unmarked_new_ones_are_reported(self):
        brain = FakeBrain({1: "your own reminder"}, {7: "your own dentist"})
        before = brain.own_things()
        brain.schedules.update({2: "TESTBATTERY stretch", 3: "stretch"})
        brain.events.update({8: "testbattery dentist"})
        left = brain.remove_test_things(before)
        self.assertEqual(brain.deleted, ["/schedules/2", "/calendar/8"])
        self.assertEqual(len(left), 1)
        self.assertIn("'stretch'", left[0])

    def test_things_that_were_there_before_are_never_removed_even_when_marked(self):
        brain = FakeBrain({1: "TESTBATTERY from an earlier run"}, {})
        left = brain.remove_test_things(brain.own_things())
        self.assertEqual(brain.deleted, [])
        self.assertEqual(left, [])

    def test_the_test_list_is_cleared_every_time(self):
        brain = FakeBrain({}, {})
        brain.remove_test_things(brain.own_things())
        self.assertEqual(brain.posted, [("/tools/list_clear", {"arguments": {"list": "testbattery"}})])

    def test_google_events_are_left_alone(self):
        brain = FakeBrain({}, {})
        self.assertEqual(brain.own_things()["events"], {})


if __name__ == "__main__":
    unittest.main()

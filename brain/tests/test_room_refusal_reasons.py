import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")
_scratch = tempfile.mkdtemp(prefix="arty-room-refusals-")
for key, name in (("BRAIN_DB", "arty.db"), ("BRAIN_FILES", "files"), ("STYLE_FILE", "style.json"),
                  ("EXPIRY_STATE", "expiry.json"), ("CALENDAR_REMINDED", "calendar-reminded.json"),
                  ("EMAIL_WATCH_STATE", "email-watch.json"), ("DESKTOP_LOOK_STATE", "desktop-look.json"),
                  ("ROOM_NOTES_STATE", "room-notes.json")):
    os.environ.setdefault(key, os.path.join(_scratch, name))

import engine_guard  # noqa: E402
import room  # noqa: E402

ROOM_HOST = "10.0.0.5"
LISTED = frozenset({ROOM_HOST})
OWNER_GAP_SECONDS = 42


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class RoomRefusalReasons(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.outside = engine_guard.OutsideWork(self.clock)
        self.gate = room.Gate(self.clock, room.OwnerActivity(self.clock, self.outside))

    def take_turn(self, most_seconds=room.ROOM_TURN_SECONDS, per_hour=room.ROOM_SECONDS_PER_HOUR):
        with self.gate.turn(most_seconds, per_hour) as turn_time:
            self.clock.now += turn_time.seconds

    def refusal(self, action):
        with self.assertLogs("brain", level="INFO") as logs, self.assertRaises(room.ApiError) as refused:
            action()
        return refused.exception.status, str(refused.exception), logs.output

    def refused_turn(self):
        return self.refusal(self.take_turn)

    def test_an_unlisted_address_logs_why_without_the_address(self):
        status, message, lines = self.refusal(lambda: self.gate.admit("10.0.0.6", LISTED))
        self.assertEqual((status, message), (401, room.INVALID_KEY))
        self.assertEqual(lines, ["WARNING:brain:room request refused: caller address is not on the room list"])
        self.assertNotIn("10.0.0.6", "".join(lines))

    def test_too_many_requests_logs_the_count_and_the_limit(self):
        for _ in range(room.REQUESTS_PER_MINUTE):
            self.gate.admit(ROOM_HOST, LISTED)
        status, message, lines = self.refusal(lambda: self.gate.admit(ROOM_HOST, LISTED))
        self.assertEqual((status, message), (429, room.TOO_MANY_REQUESTS))
        self.assertEqual(lines, ["WARNING:brain:room request refused: 30 requests in the last minute (limit 30)"])

    def test_too_many_turns_in_a_minute_logs_the_minute_limit(self):
        for _ in range(room.TURNS_PER_MINUTE):
            self.take_turn(1)
        status, message, lines = self.refused_turn()
        self.assertEqual((status, message), (429, room.TOO_MANY_TURNS))
        self.assertEqual(lines, ["WARNING:brain:room turn refused: 4 turns in the last minute (limit 4)"])

    def test_too_many_turns_in_an_hour_logs_the_hour_limit(self):
        for _ in range(room.TURNS_PER_HOUR):
            self.take_turn(1)
            self.clock.now += room.MINUTE_SECONDS / room.TURNS_PER_MINUTE
        status, message, lines = self.refused_turn()
        self.assertEqual((status, message), (429, room.TOO_MANY_TURNS))
        self.assertEqual(lines, ["WARNING:brain:room turn refused: 60 turns in the last hour (limit 60)"])

    def test_a_spent_hour_logs_the_seconds_used_and_the_allowance(self):
        for _ in range(room.ROOM_SECONDS_PER_HOUR // room.ROOM_TURN_SECONDS):
            self.take_turn()
            self.clock.now += room.MINUTE_SECONDS
        self.gate.spent.add(12)
        status, message, lines = self.refused_turn()
        self.assertEqual((status, message), (429, room.TOO_MUCH_TIME))
        self.assertEqual(lines, ["WARNING:brain:room turn refused: hourly time budget used (1812 of 1800 s)"])

    def test_a_spent_hour_from_the_config_logs_that_allowance(self):
        self.take_turn(room.ROOM_TURN_SECONDS, 100)
        self.clock.now += room.MINUTE_SECONDS
        status, message, lines = self.refusal(lambda: self.take_turn(room.ROOM_TURN_SECONDS, 100))
        self.assertEqual((status, message), (429, room.TOO_MUCH_TIME))
        self.assertEqual(lines, ["WARNING:brain:room turn refused: hourly time budget used (100 of 100 s)"])

    def test_an_owner_seen_recently_logs_how_long_ago(self):
        with self.gate.owner.chatting():
            pass
        self.clock.now += OWNER_GAP_SECONDS
        status, message, lines = self.refused_turn()
        self.assertEqual((status, message), (429, room.OWNER_BUSY))
        self.assertEqual(lines, ["INFO:brain:room turn refused: owner active 42 s ago"])

    def test_an_owner_chatting_now_logs_that(self):
        with self.gate.owner.chatting():
            self.clock.now += OWNER_GAP_SECONDS
            status, message, lines = self.refused_turn()
        self.assertEqual((status, message), (429, room.OWNER_BUSY))
        self.assertEqual(lines, ["INFO:brain:room turn refused: owner chatting right now"])

    def test_outside_work_running_now_logs_that(self):
        with self.outside.working():
            status, message, lines = self.refused_turn()
        self.assertEqual((status, message), (429, room.OWNER_BUSY))
        self.assertEqual(lines, ["INFO:brain:room turn refused: outside work running right now"])

    def test_a_turn_already_running_logs_that(self):
        with self.gate.turn():
            status, message, lines = self.refused_turn()
        self.assertEqual((status, message), (429, room.BUSY))
        self.assertEqual(lines, ["INFO:brain:room turn refused: another room turn is still running"])

    def test_an_admitted_request_and_turn_log_no_refusal(self):
        with self.assertNoLogs("brain", level="INFO"):
            self.gate.admit(ROOM_HOST, LISTED)
            self.take_turn()


if __name__ == "__main__":
    unittest.main()

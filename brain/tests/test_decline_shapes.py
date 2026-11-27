import sys
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_remote_rules import (CLIENT_TOOLS, CONFIG, REMOTE, REMOTE_UNRESTRICTED, SELF_HARM_REQUEST, WEAPON_REQUEST,  # noqa: E402
                               FakeEngine, RemoteTurns)

import brain  # noqa: E402
import connections  # noqa: E402
import replies  # noqa: E402
import starfire_rules  # noqa: E402

from api_support import ApiError  # noqa: E402

MODEL_ANSWER = {"role": "assistant", "content": "MODEL-ANSWERED"}
GO_ON = "go on, in detail"
PADDING = "x " * 10001
LONG_RUN = 50_000
LONG_RUN_SECONDS = 1.0


def rules(said, model="remote-model-a", **extra):
    return starfire_rules.rules_for(CONFIG, {"model": model, "said": said, **extra})


class EverythingStarfireSendsIsChecked(unittest.TestCase):
    def test_a_request_after_long_padding_is_declined(self):
        self.assertEqual(rules(PADDING + WEAPON_REQUEST)["declined_kind"], "weapons")

    def test_a_request_before_long_padding_is_declined(self):
        self.assertEqual(rules(WEAPON_REQUEST + PADDING)["declined_kind"], "weapons")

    def test_a_request_across_the_join_of_two_checked_pieces_is_declined(self):
        lead = "x" * (starfire_rules.SAID_PIECE_CHARS - len(WEAPON_REQUEST) // 2 - 1) + " "
        self.assertEqual(rules(lead + WEAPON_REQUEST)["declined_kind"], "weapons")

    def test_a_cry_for_help_after_padding_gets_the_help_reply_on_any_model(self):
        self.assertEqual(rules(PADDING + SELF_HARM_REQUEST, model="remote-unruled")["declined_kind"], "self_harm")

    def test_ordinary_long_text_is_not_declined(self):
        self.assertIsNone(rules("tell me about otters. " * 5000)["declined"])

    def test_text_too_long_to_check_is_refused_not_cut(self):
        with self.assertRaises(ApiError) as refused:
            rules("x" * (starfire_rules.MAX_CHECKED_CHARS + 1))
        self.assertEqual(refused.exception.status, starfire_rules.SAID_TOO_LONG)


class StarfireMayOnlyNarrowUnrestricted(unittest.TestCase):
    def test_a_name_the_brain_lists_keeps_the_safety_rules_when_starfire_says_restricted(self):
        told = rules(WEAPON_REQUEST, model="remote-unruled", unrestricted=False)
        self.assertEqual(told["declined_kind"], "weapons")
        self.assertTrue(told["safety_rules"])

    def test_a_name_the_brain_lists_is_unrestricted_when_starfire_agrees(self):
        told = rules(WEAPON_REQUEST, model="remote-unruled", unrestricted=True)
        self.assertIsNone(told["declined"])
        self.assertFalse(told["safety_rules"])


class OwnChatsCheckEveryUnansweredMessage(RemoteTurns):
    def turn(self, model, messages, agent=False):
        self.engine = FakeEngine(MODEL_ANSWER)
        with unittest.mock.patch.object(connections, "http_json", side_effect=self.engine):
            return brain.run_turn(messages, f"arty/{model}", any_installed=True, agent=agent,
                                  client_tools=CLIENT_TOOLS if agent else None)

    def test_a_request_followed_by_go_on_is_declined_on_a_remote_model(self):
        _, used, *_ = self.turn(REMOTE, [{"role": "user", "content": WEAPON_REQUEST},
                                         {"role": "user", "content": GO_ON}])
        self.assertEqual(used, ["safety_weapons"])
        self.assertEqual(self.engine.chat_bodies(), [])

    def test_a_request_followed_by_go_on_is_declined_on_a_local_model(self):
        _, used, *_ = self.turn("qwen3:14b", [{"role": "user", "content": WEAPON_REQUEST},
                                             {"role": "user", "content": GO_ON}])
        self.assertEqual(used, ["safety_weapons"])

    def test_an_agent_turn_checks_every_message_since_the_last_reply(self):
        _, used, *_ = self.turn(REMOTE, [{"role": "user", "content": WEAPON_REQUEST},
                                         {"role": "user", "content": GO_ON}], agent=True)
        self.assertEqual(used, ["safety_weapons"])
        self.assertEqual(self.engine.chat_bodies(), [])

    def test_a_request_already_answered_is_not_checked_again(self):
        answer, *_ = self.turn(REMOTE, [{"role": "user", "content": WEAPON_REQUEST},
                                        {"role": "assistant", "content": "I can't help with that."},
                                        {"role": "user", "content": "tell me about otters"}])
        self.assertEqual(answer, "MODEL-ANSWERED")

    def test_an_unrestricted_remote_model_still_answers_ordinary_follow_ups(self):
        answer, *_ = self.turn(REMOTE_UNRESTRICTED, [{"role": "user", "content": "hello"},
                                                    {"role": "user", "content": GO_ON}])
        self.assertEqual(answer, "MODEL-ANSWERED")


class DashCleaningTakesTimeInLineWithLength(unittest.TestCase):
    def test_a_long_run_of_spaces_or_tabs_without_a_dash_is_quick(self):
        for run in (" ", "\t", " \t"):
            with self.subTest(run=repr(run)):
                text = "a" + run * LONG_RUN + "b"
                started = time.perf_counter()
                self.assertEqual(replies.without_em_dashes(text), text)
                self.assertLess(time.perf_counter() - started, LONG_RUN_SECONDS)


if __name__ == "__main__":
    unittest.main()

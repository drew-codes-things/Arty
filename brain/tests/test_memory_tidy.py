import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

import brain  # noqa: E402
import connections  # noqa: E402
import memory_facts  # noqa: E402
import scheduler  # noqa: E402
import memory_tidy  # noqa: E402

NIGHT = datetime(2026, 11, 12, 4, 10).timestamp()
DAYTIME = datetime(2026, 11, 12, 15, 0).timestamp()


class FakeMemory:
    def __init__(self, entities):
        self.entities = {item["name"]: dict(item, observations=list(item["observations"])) for item in entities}
        self.calls = []
        self.fail_on = None

    def call(self, route, body):
        self.calls.append(route)
        if route == self.fail_on:
            raise OSError("hub down")
        if route == "read_graph":
            return {"entities": [dict(item, observations=list(item["observations"])) for item in self.entities.values()]}
        if route == "delete_observations":
            for deletion in body["deletions"]:
                kept = self.entities[deletion["entityName"]]["observations"]
                kept[:] = [item for item in kept if item not in deletion["observations"]]
        elif route == "add_observations":
            for addition in body["observations"]:
                kept = self.entities[addition["entityName"]]["observations"]
                kept.extend(item for item in addition["contents"] if item not in kept)
        elif route == "create_entities":
            for item in body["entities"]:
                self.entities.setdefault(item["name"], dict(item, observations=list(item["observations"])))
        return {}

    def facts(self, name):
        return self.entities[name]["observations"]


def no_model(prompt):
    raise AssertionError("the model was not expected to be asked")


def replies(answer):
    asked = []

    def complete(prompt):
        asked.append(prompt)
        return json.dumps(answer)
    complete.asked = asked
    return complete


class Scratch(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.archive = Path(folder.name) / "archived-facts.jsonl"
        self.state = Path(folder.name) / "memory-tidy.json"
        self.files = {"archive_file": self.archive, "state_file": self.state}

    def archived(self):
        return [json.loads(line) for line in self.archive.read_text().splitlines()]


class Duplicates(Scratch):
    def test_a_fact_saved_twice_in_different_case_keeps_the_newest_wording(self):
        memory = FakeMemory([{"name": "Alex", "entityType": "person",
                              "observations": ["alex likes tea.", "Alex lives in York", "Alex likes tea"]}])
        memory_tidy.tidy(memory.call, no_model, now=NIGHT, use_model=False, **self.files)
        self.assertEqual(memory.facts("Alex"), ["Alex lives in York", "Alex likes tea"])
        [record] = self.archived()
        self.assertEqual((record["entity"], record["fact"]), ("Alex", "alex likes tea."))
        self.assertIn("same as", record["reason"])

    def test_nothing_to_tidy_writes_nothing(self):
        memory = FakeMemory([{"name": "Alex", "entityType": "person", "observations": ["Alex likes tea"]}])
        summary = memory_tidy.tidy(memory.call, no_model, now=NIGHT, use_model=False, **self.files)
        self.assertEqual(summary["archived"], 0)
        self.assertNotIn("delete_observations", memory.calls)
        self.assertFalse(self.archive.exists())


class Replaced(Scratch):
    def test_a_fact_a_later_one_replaces_is_archived_with_what_replaced_it(self):
        memory = FakeMemory([{"name": "Alex", "entityType": "person",
                              "observations": ["Alex lives in York", "Alex likes tea", "Alex moved to Leeds in October"]}])
        complete = replies({"replaced": [{"old": 0, "new": 2}]})
        memory_tidy.tidy(memory.call, complete, now=NIGHT, **self.files)
        self.assertEqual(memory.facts("Alex"), ["Alex likes tea", "Alex moved to Leeds in October"])
        [record] = self.archived()
        self.assertEqual(record["fact"], "Alex lives in York")
        self.assertIn("Alex moved to Leeds in October", record["reason"])
        self.assertIn("0. Alex lives in York", complete.asked[0])

    def test_the_model_cannot_archive_a_newer_fact_or_point_outside_the_list(self):
        memory = FakeMemory([{"name": "Alex", "entityType": "person", "observations": ["A fact", "B fact", "C fact"]}])
        bad = {"replaced": [{"old": 2, "new": 0}, {"old": 1, "new": 1}, {"old": 5, "new": 6}, {"old": "0", "new": 1},
                            {"old": True, "new": 2}, {"old": -1, "new": 2}, "junk", {"old": 0}]}
        summary = memory_tidy.tidy(memory.call, replies(bad), now=NIGHT, **self.files)
        self.assertEqual((summary["archived"], memory.facts("Alex")), (0, ["A fact", "B fact", "C fact"]))

    def test_a_reply_that_is_not_json_changes_nothing(self):
        memory = FakeMemory([{"name": "Alex", "entityType": "person", "observations": ["A fact", "B fact"]}])
        summary = memory_tidy.tidy(memory.call, lambda prompt: "Sure! Fact 0 is old.", now=NIGHT, **self.files)
        self.assertEqual((summary["archived"], memory.facts("Alex")), (0, ["A fact", "B fact"]))

    def test_an_entity_with_one_fact_is_not_sent_to_the_model(self):
        memory = FakeMemory([{"name": "Alex", "entityType": "person", "observations": ["Alex likes tea"]}])
        memory_tidy.tidy(memory.call, no_model, now=NIGHT, **self.files)


class NeverLost(Scratch):
    def test_facts_are_written_to_the_archive_before_they_leave_memory(self):
        memory = FakeMemory([{"name": "Alex", "entityType": "person", "observations": ["Alex likes Tea", "Alex likes tea"]}])
        memory.fail_on = "delete_observations"
        with self.assertRaises(OSError):
            memory_tidy.tidy(memory.call, no_model, now=NIGHT, use_model=False, **self.files)
        self.assertEqual([record["fact"] for record in self.archived()], ["Alex likes Tea"])

    def test_undo_puts_the_last_run_back_and_only_once(self):
        memory = FakeMemory([{"name": "Alex", "entityType": "person", "observations": ["Alex lives in York", "Alex moved to Leeds"]},
                             {"name": "Sam", "entityType": "person", "observations": ["sam is a vet", "Sam is a vet"]}])
        memory_tidy.tidy(memory.call, replies({"replaced": [{"old": 0, "new": 1}]}), now=NIGHT, **self.files)
        del memory.entities["Sam"]
        reply = memory_tidy.restore_last_run(memory.call, now=NIGHT + 60, **self.files)
        self.assertIn("2", reply)
        self.assertEqual(memory.facts("Alex"), ["Alex moved to Leeds", "Alex lives in York"])
        self.assertEqual(memory.facts("Sam"), ["sam is a vet"])
        again = memory_tidy.restore_last_run(memory.call, now=NIGHT + 120, **self.files)
        self.assertIn("nothing", again.lower())

    def test_undo_with_no_archive_says_so(self):
        reply = memory_tidy.restore_last_run(FakeMemory([]).call, now=NIGHT, **self.files)
        self.assertIn("nothing", reply.lower())


class WhenItRuns(Scratch):
    def test_it_runs_once_a_night_in_the_tidy_hour(self):
        self.assertTrue(memory_tidy.due({}, now=NIGHT, state_file=self.state))
        self.assertFalse(memory_tidy.due({}, now=DAYTIME, state_file=self.state))
        memory_tidy.tidy(FakeMemory([]).call, no_model, now=NIGHT, **self.files)
        self.assertFalse(memory_tidy.due({}, now=NIGHT + 600, state_file=self.state))
        self.assertTrue(memory_tidy.due({}, now=NIGHT + 24 * 3600, state_file=self.state))

    def test_it_can_be_switched_off_or_moved(self):
        self.assertFalse(memory_tidy.due({"enabled": False}, now=NIGHT, state_file=self.state))
        afternoon = {"hour": 15}
        self.assertTrue(memory_tidy.due(afternoon, now=DAYTIME, state_file=self.state))
        self.assertTrue(memory_tidy.due({"hour": "junk"}, now=NIGHT, state_file=self.state))

    def test_the_undo_request_is_recognised(self):
        for said in ("undo the memory tidy-up", "Arty, undo last night's memory clean up", "bring back the archived facts",
                     "please restore the archived memories"):
            self.assertTrue(memory_tidy.is_restore_request(said), said)
        for said in ("undo that", "tidy up my memory", "what did you archive?"):
            self.assertFalse(memory_tidy.is_restore_request(said), said)


class BrainWiring(unittest.TestCase):
    def test_the_undo_request_restores_through_the_memory_hub(self):
        with unittest.mock.patch.object(memory_tidy, "restore_last_run", return_value="put back") as restore:
            self.assertEqual(brain.restore_memory_reply("undo the memory tidy-up"), "put back")
            self.assertIsNone(brain.restore_memory_reply("undo that"))
        restore.assert_called_once_with(memory_facts.memory_hub)

    def test_the_memory_hub_calls_the_memory_server_with_the_key(self):
        with unittest.mock.patch.object(connections, "http_json", return_value={}) as sent:
            memory_facts.memory_hub("read_graph", {})
        self.assertTrue(sent.call_args.args[0].endswith("/memory/read_graph"))
        self.assertIn("Authorization", sent.call_args.kwargs["headers"])

    def test_the_scheduler_starts_a_tidy_only_when_it_is_due(self):
        for is_due, starts in ((True, 1), (False, 0)):
            with unittest.mock.patch.object(memory_tidy, "due", return_value=is_due), \
                    unittest.mock.patch.object(connections, "load_config", return_value={}), \
                    unittest.mock.patch.object(brain.threading, "Thread") as thread:
                scheduler.start_memory_tidy()
            self.assertEqual(thread.call_count, starts)
            if starts:
                self.assertIs(thread.call_args.kwargs["target"], memory_tidy.run_in_background)

    def test_every_scheduler_tick_checks_the_tidy(self):
        with unittest.mock.patch.object(scheduler, "due_schedules_now", return_value=[]), \
                unittest.mock.patch.object(scheduler, "guarded") as guarded:
            scheduler.scheduler_tick(datetime(2026, 11, 12, 4, 0))
        self.assertIn("memory tidy", [call.args[0] for call in guarded.call_args_list])

    def test_the_tidy_asks_for_json_without_thinking(self):
        with unittest.mock.patch.object(scheduler, "complete_text", return_value="{}") as complete:
            scheduler.tidy_completion("prompt")
        complete.assert_called_once_with("prompt", json_mode=True, think=False)


if __name__ == "__main__":
    unittest.main()

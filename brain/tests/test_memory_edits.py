import json
import os
import re
import sys
import unittest
import unittest.mock
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

from test_review_brain import Server  # noqa: E402

import brain  # noqa: E402
import devices  # noqa: E402
import memory_edits  # noqa: E402
import store  # noqa: E402
from api_support import ApiError  # noqa: E402

API_DOCS = Path(__file__).resolve().parents[2] / "docs" / "API.md"


class FakeMemory:
    def __init__(self, entities=None):
        self.entities = [dict(entity, observations=list(entity["observations"])) for entity in entities or []]
        self.calls = []
        self.fail_on = None

    def named(self, name):
        return next(entity for entity in self.entities if entity["name"] == name)

    def __call__(self, route, body):
        self.calls.append(route)
        if route == self.fail_on:
            raise urllib.error.URLError("memory store down")
        if route == "read_graph":
            return {"entities": [dict(entity, observations=list(entity["observations"])) for entity in self.entities], "relations": []}
        if route == "create_entities":
            self.entities += [dict(entity, observations=list(entity["observations"])) for entity in body["entities"]]
        elif route == "add_observations":
            for item in body["observations"]:
                self.named(item["entityName"])["observations"] += item["contents"]
        elif route == "delete_observations":
            for item in body["deletions"]:
                entity = self.named(item["entityName"])
                entity["observations"] = [o for o in entity["observations"] if o not in item["observations"]]
        elif route == "delete_entities":
            self.entities = [entity for entity in self.entities if entity["name"] not in body["entityNames"]]
        return {}


SAVED = [{"name": "Rowan", "entityType": "person", "observations": ["Lives in Bristol", "Plays five-a-side on Thursdays"]},
         {"name": "Ella", "entityType": "person", "observations": ["Rowan's sister"]}]


class Edits(unittest.TestCase):
    def setUp(self):
        self.memory = FakeMemory(SAVED)

    def test_a_fact_is_added_to_an_existing_entity_whatever_the_case_of_its_name(self):
        self.assertEqual(memory_edits.add(self.memory, {"entity": "rowan", "fact": "Supports Bristol Rovers"}),
                         {"entity": "Rowan", "fact": "Supports Bristol Rovers"})
        self.assertEqual(self.memory.named("Rowan")["observations"][-1], "Supports Bristol Rovers")

    def test_a_new_entity_is_made_with_its_first_fact(self):
        memory_edits.add(self.memory, {"entity": "Biscuit", "fact": "Rowan's dog", "type": "pet"})
        self.assertEqual(self.memory.named("Biscuit"), {"name": "Biscuit", "entityType": "pet", "observations": ["Rowan's dog"]})
        memory_edits.add(self.memory, {"entity": "Garden", "fact": "Has a pond"})
        self.assertEqual(self.memory.named("Garden")["entityType"], "topic")

    def test_a_fact_is_edited_in_place_of_the_old_one(self):
        memory_edits.edit(self.memory, {"entity": "Rowan", "old": "Lives in Bristol", "new": "Lives in Bath"})
        self.assertEqual(sorted(self.memory.named("Rowan")["observations"]), ["Lives in Bath", "Plays five-a-side on Thursdays"])

    def test_a_failed_save_puts_the_old_fact_back(self):
        calls = []

        def flaky(route, body):
            calls.append((route, body))
            if route == "add_observations" and body["observations"][0]["contents"] == ["Lives in Bath"]:
                raise urllib.error.URLError("down")
            return self.memory(route, body)

        with self.assertRaises(urllib.error.URLError):
            memory_edits.edit(flaky, {"entity": "Rowan", "old": "Lives in Bristol", "new": "Lives in Bath"})
        self.assertIn("Lives in Bristol", self.memory.named("Rowan")["observations"])

    def test_a_fact_or_a_whole_entity_is_deleted(self):
        memory_edits.delete(self.memory, {"entity": "Rowan", "fact": "Lives in Bristol"})
        self.assertEqual(self.memory.named("Rowan")["observations"], ["Plays five-a-side on Thursdays"])
        self.assertEqual(memory_edits.delete(self.memory, {"entity": "ella"}), {"entity": "Ella", "removed": "entity"})
        self.assertEqual([entity["name"] for entity in self.memory.entities], ["Rowan"])

    def test_bad_input_is_refused_before_the_store_is_touched(self):
        cases = [
            (memory_edits.add, {"entity": "", "fact": "x"}, 400),
            (memory_edits.add, {"entity": "Rowan", "fact": 5}, 400),
            (memory_edits.add, {"entity": "x" * 61, "fact": "y"}, 400),
            (memory_edits.add, {"entity": "Rowan", "fact": "y" * 301}, 400),
            (memory_edits.add, {"entity": "Rowan", "fact": "bell\x07"}, 400),
            (memory_edits.add, {"entity": "Rowan", "fact": "Wi-Fi password is hunter2"}, 400),
            (memory_edits.edit, {"entity": "Rowan", "old": "Lives in Bristol", "new": "Bank card number 4111"}, 400),
            (memory_edits.add, {"entity": "Rowan", "fact": "x", "type": ["person"]}, 400),
        ]
        for action, request, status in cases:
            with self.subTest(request=request), self.assertRaises(ApiError) as raised:
                action(self.memory, request)
            self.assertEqual(raised.exception.status, status)
        self.assertNotIn("create_entities", self.memory.calls)
        self.assertNotIn("add_observations", self.memory.calls)
        self.assertNotIn("delete_observations", self.memory.calls)

    def test_missing_things_and_duplicates_are_reported(self):
        cases = [
            (memory_edits.edit, {"entity": "Nobody", "old": "a", "new": "b"}, 404),
            (memory_edits.edit, {"entity": "Rowan", "old": "Lives in Leeds", "new": "b"}, 404),
            (memory_edits.delete, {"entity": "Rowan", "fact": "Lives in Leeds"}, 404),
            (memory_edits.delete, {"entity": "Nobody"}, 404),
            (memory_edits.add, {"entity": "Rowan", "fact": "lives in bristol"}, 409),
        ]
        for action, request, status in cases:
            with self.subTest(request=request), self.assertRaises(ApiError) as raised:
                action(self.memory, request)
            self.assertEqual(raised.exception.status, status)

    def test_spaces_and_line_breaks_are_tidied(self):
        memory_edits.add(self.memory, {"entity": "  Rowan ", "fact": " Likes\n  tea  "})
        self.assertEqual(self.memory.named("Rowan")["observations"][-1], "Likes tea")


LONG_FACT = "x" * 301
LONG_NAME = "A name that is well over the sixty character cap for entity names here"
ODD_FACTS = ["Two  spaces here", "Line one\nline two", LONG_FACT, "tab\there"]
ODD = [{"name": "Sam", "entityType": "person", "observations": ["Likes tea", *ODD_FACTS]},
       {"name": LONG_NAME, "entityType": "topic", "observations": ["a"]},
       {"name": "Two  spaces", "entityType": "topic", "observations": ["a"]}]
HIDDEN_OR_SECRET = {
    "secret in the entity name": {"entity": "Wi-Fi password hunter2", "fact": "see the name"},
    "secret in the type": {"entity": "Router", "fact": "see the type", "type": "password hunter2"},
    "zero width inside a word": {"entity": "Sam", "fact": "Wi-Fi pass\u200bword is hunter2"},
    "bidi override": {"entity": "Sam", "fact": "\u202eevil text\u202c shown backwards"},
    "C1 control": {"entity": "Sam", "fact": "bell\x9b[31m"},
    "zero width twin of a name": {"entity": "Sa\u200bm", "fact": "a second Sam"},
    "zero width in the type": {"entity": "Router", "fact": "x", "type": "to\u200bpic"},
}


class FactsOtherWritersSaved(unittest.TestCase):
    def test_each_odd_fact_can_be_deleted_by_its_stored_or_folded_text(self):
        for fact in ODD_FACTS:
            for asked in (fact, " ".join(fact.split())):
                with self.subTest(fact=fact[:20], asked=asked[:20]):
                    memory = FakeMemory(ODD)
                    self.assertEqual(memory_edits.delete(memory, {"entity": "Sam", "fact": asked}), {"entity": "Sam", "removed": "fact"})
                    self.assertNotIn(fact, memory.named("Sam")["observations"])
                    self.assertIn("Likes tea", memory.named("Sam")["observations"])

    def test_each_odd_fact_can_be_edited(self):
        for fact in ODD_FACTS:
            with self.subTest(fact=fact[:20]):
                memory = FakeMemory(ODD)
                memory_edits.edit(memory, {"entity": "Sam", "old": fact, "new": "Fixed now"})
                self.assertEqual(memory.named("Sam")["observations"].count("Fixed now"), 1)
                self.assertNotIn(fact, memory.named("Sam")["observations"])

    def test_entities_with_long_or_odd_names_can_be_deleted(self):
        for name, asked in ((LONG_NAME, LONG_NAME), ("Two  spaces", "Two  spaces"), ("Two  spaces", "two spaces")):
            with self.subTest(asked=asked):
                memory = FakeMemory(ODD)
                memory_edits.delete(memory, {"entity": asked})
                self.assertNotIn(name, [entity["name"] for entity in memory.entities])

    def test_a_fact_added_to_an_odd_name_goes_to_that_entity(self):
        memory = FakeMemory(ODD)
        self.assertEqual(memory_edits.add(memory, {"entity": "two spaces", "fact": "b"}), {"entity": "Two  spaces", "fact": "b"})
        self.assertEqual(len(memory.entities), 3)

    def test_new_text_is_still_capped(self):
        cases = [(memory_edits.add, {"entity": "Sam", "fact": LONG_FACT}), (memory_edits.edit, {"entity": "Sam", "old": "Likes tea", "new": LONG_FACT}),
                 (memory_edits.add, {"entity": LONG_NAME, "fact": "a"})]
        for action, request in cases:
            with self.subTest(request=str(request)[:40]), self.assertRaises(ApiError) as raised:
                action(FakeMemory(ODD), request)
            self.assertEqual(raised.exception.status, 400)


class HiddenCharactersAndSecrets(unittest.TestCase):
    def test_secrets_anywhere_and_hidden_characters_are_refused_before_the_store_is_touched(self):
        for label, request in HIDDEN_OR_SECRET.items():
            with self.subTest(label):
                memory = FakeMemory(ODD)
                with self.assertRaises(ApiError) as raised:
                    memory_edits.add(memory, request)
                self.assertEqual(raised.exception.status, 400)
                self.assertEqual(memory.calls, [])

    def test_an_edit_to_a_hidden_character_or_secret_is_refused(self):
        for new in ("pass\u200bword hunter2", "\u202ebackwards", "bell\x9b"):
            with self.subTest(new=new), self.assertRaises(ApiError):
                memory_edits.edit(FakeMemory(ODD), {"entity": "Sam", "old": "Likes tea", "new": new})


class StoreTrouble(unittest.TestCase):
    def test_an_edit_saves_the_new_fact_before_removing_the_old(self):
        memory = FakeMemory(ODD)
        memory_edits.edit(memory, {"entity": "Sam", "old": "Likes tea", "new": "Likes coffee"})
        writes = [route for route in memory.calls if route != "read_graph"]
        self.assertEqual(writes, ["add_observations", "delete_observations"])

    def test_a_store_that_goes_down_after_one_write_keeps_both_facts(self):
        memory = FakeMemory([{"name": "Sam", "entityType": "person", "observations": ["Likes tea"]}])
        writes = []

        def down_after_one_write(route, body):
            if route != "read_graph":
                if writes:
                    raise urllib.error.URLError("down")
                writes.append(route)
            return memory(route, body)

        with self.assertRaises(urllib.error.URLError):
            memory_edits.edit(down_after_one_write, {"entity": "Sam", "old": "Likes tea", "new": "Likes coffee"})
        self.assertEqual(memory.named("Sam")["observations"], ["Likes tea", "Likes coffee"])

    def test_a_new_name_another_writer_made_meanwhile_still_gets_the_fact(self):
        memory = FakeMemory([])

        def like_the_real_store(route, body):
            if route == "create_entities":
                known = {entity["name"] for entity in memory.entities}
                body = {"entities": [entity for entity in body["entities"] if entity["name"] not in known]}
            return memory(route, body)

        def another_writer_gets_in_first(route, body):
            if route == "create_entities":
                like_the_real_store(route, {"entities": [{"name": "Sam", "entityType": "person", "observations": ["From the chat"]}]})
            return like_the_real_store(route, body)

        memory_edits.add(another_writer_gets_in_first, {"entity": "Sam", "fact": "From the app"})
        self.assertEqual(memory.named("Sam")["observations"], ["From the chat", "From the app"])


class Routes(Server):
    def setUp(self):
        super().setUp()
        self.memory = FakeMemory(SAVED)
        patcher = unittest.mock.patch.object(brain, "memory_hub", self.memory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, action, body, key=None):
        headers = {"Authorization": f"Bearer {key}"} if key else None
        status, reply = self.request("POST", f"/v1/memory/{action}", body=body, headers=headers)
        return status, json.loads(reply)

    def test_the_main_key_adds_edits_and_deletes(self):
        self.assertEqual(self.post("add", {"entity": "Rowan", "fact": "Supports Bristol Rovers"})[0], 201)
        self.assertEqual(self.post("edit", {"entity": "Rowan", "old": "Lives in Bristol", "new": "Lives in Bath"})[0], 200)
        self.assertEqual(self.post("delete", {"entity": "Ella"})[0], 200)
        self.assertEqual(self.memory.named("Rowan")["observations"], ["Plays five-a-side on Thursdays", "Supports Bristol Rovers", "Lives in Bath"])

    def test_an_approved_device_may_edit_and_a_revoked_or_unknown_key_may_not(self):
        made = brain.make_device_key("Rowan's phone", "127.0.0.1")
        self.assertEqual(self.post("add", {"entity": "Rowan", "fact": "Owns a Pixel"}, key=made["api_key"])[0], 201)
        store.revoke_device(made["id"])
        self.assertEqual(self.post("add", {"entity": "Rowan", "fact": "Owns a kettle"}, key=made["api_key"])[0], 401)
        self.assertEqual(self.post("add", {"entity": "Rowan", "fact": "Owns a kettle"}, key=devices.DEVICE_KEY_PREFIX + "made-up")[0], 401)
        self.assertNotIn("Owns a kettle", self.memory.named("Rowan")["observations"])

    def test_a_down_store_is_a_503_and_bad_input_a_400(self):
        self.memory.fail_on = "read_graph"
        status, reply = self.post("add", {"entity": "Rowan", "fact": "Likes tea"})
        self.assertEqual((status, reply["error"]["message"]), (503, brain.MEMORY_DOWN))
        self.memory.fail_on = None
        self.assertEqual(self.post("add", {"entity": "Rowan"})[0], 400)
        self.assertEqual(self.post("rename", {"entity": "Rowan"})[0], 404)

    def test_an_odd_whitespace_fact_is_deleted_through_the_route(self):
        self.memory.entities.append({"name": "Sam", "entityType": "person", "observations": ["Two  spaces\there"]})
        self.assertEqual(self.post("delete", {"entity": "Sam", "fact": "Two  spaces\there"}), (200, {"entity": "Sam", "removed": "fact"}))
        self.assertEqual(self.memory.named("Sam")["observations"], [])


    def test_every_key_the_brain_accepts_may_change_memory(self):
        made = brain.make_device_key("Rowan's tablet", "127.0.0.1")
        self.assertFalse(next(row for row in store.list_devices() if row["id"] == made["id"])["remote_control"])
        self.assertEqual(self.post("add", {"entity": "Rowan", "fact": "Owns a tablet"}, key=made["api_key"])[0], 201)
        self.assertEqual(self.post("edit", {"entity": "Rowan", "old": "Owns a tablet", "new": "Owns two tablets"}, key=made["api_key"])[0], 200)
        self.assertEqual(self.post("delete", {"entity": "Ella"}, key=made["api_key"])[0], 200)
        self.assertEqual(self.post("add", {"entity": "Rowan", "fact": "Owns a phone"})[0], 201)


class ApiDocs(unittest.TestCase):
    def test_the_docs_say_any_accepted_key_may_change_memory_and_promise_no_403(self):
        paragraph = next(line for line in API_DOCS.read_text().splitlines() if line.startswith("- `POST /v1/memory/add`"))
        self.assertIn("any key the brain accepts", paragraph.casefold())
        self.assertIn("remote control off", paragraph)
        self.assertNotRegex(paragraph, re.compile(r"\b403\b"))


if __name__ == "__main__":
    unittest.main()

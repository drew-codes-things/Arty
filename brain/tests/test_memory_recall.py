import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

import brain  # noqa: E402
import memory_recall  # noqa: E402

BUDGET = 4000
FILLER_FACTS = 120


def entity(name, kind, observations):
    return {"name": name, "entityType": kind, "observations": observations}


def big_graph():
    oldest = entity("Sam", "person", ["Sam is the user's sister and works as a vet in Leeds"])
    filler = entity("Alex", "person", [f"Alex finished chore number {index} of the long weekly cleaning rota at home"
                                       for index in range(FILLER_FACTS)])
    return {"entities": [oldest, filler], "relations": []}


class RecallKeepsWhatMatters(unittest.TestCase):
    def test_an_old_fact_that_matches_the_request_is_recalled_past_the_budget(self):
        text = memory_recall.memory_lines(big_graph(), "what does my sister Sam do for work?", BUDGET)
        self.assertIn("works as a vet in Leeds", text)

    def test_the_entity_name_alone_is_enough_to_match(self):
        text = memory_recall.memory_lines(big_graph(), "how is Sam getting on", BUDGET)
        self.assertIn("vet in Leeds", text)

    def test_words_match_across_endings(self):
        text = memory_recall.memory_lines(big_graph(), "anything about sisters?", BUDGET)
        self.assertIn("vet in Leeds", text)

    def test_with_nothing_relevant_the_newest_facts_fill_the_budget(self):
        text = memory_recall.memory_lines(big_graph(), "hello there", BUDGET)
        self.assertIn(f"chore number {FILLER_FACTS - 1} ", text)
        self.assertNotIn("vet in Leeds", text)

    def test_the_text_fits_the_budget_and_never_cuts_a_fact(self):
        for request in ("", "what does Sam do", "chore rota"):
            text = memory_recall.memory_lines(big_graph(), request, BUDGET)
            self.assertLessEqual(len(text), BUDGET)
            for line in text.splitlines():
                self.assertTrue(line.startswith("- "), line)
                for fact in line.split(": ", 1)[1].split("; "):
                    self.assertTrue(fact.startswith(("Sam is", "Alex finished")), fact)

    def test_facts_keep_their_entity_and_original_order(self):
        graph = {"entities": [entity("Alex", "person", ["Alex likes tea", "Alex lives in York", "Alex owns a cat"])]}
        self.assertEqual(memory_recall.memory_lines(graph, "tea", BUDGET),
                         "- Alex (person): Alex likes tea; Alex lives in York; Alex owns a cat")

    def test_a_small_memory_is_recalled_whole(self):
        graph = {"entities": [entity("Alex", "person", ["Alex likes tea"]), entity("Rust", "topic", ["Alex is learning Rust"])]}
        self.assertEqual(memory_recall.memory_lines(graph, "", BUDGET),
                         "- Alex (person): Alex likes tea\n- Rust (topic): Alex is learning Rust")

    def test_search_syntax_in_the_request_is_read_as_plain_words(self):
        for request in ('"vet" OR', "NEAR(Sam vet)", "Sam*", "sister AND -vet", "^Sam", "column:vet", "Sam's"):
            memory_recall.memory_lines(big_graph(), request, BUDGET)

    def test_an_empty_or_odd_graph_gives_no_lines(self):
        for graph in (None, {}, {"entities": None}, {"entities": [{"name": "Alex"}]}, {"entities": ["junk"]}):
            self.assertEqual(memory_recall.memory_lines(graph, "tea", BUDGET), "")

    def test_a_fact_longer_than_what_is_left_is_skipped_for_smaller_ones(self):
        graph = {"entities": [entity("Alex", "person", ["x" * 60, "Alex likes tea"])]}
        self.assertEqual(memory_recall.memory_lines(graph, "", 40), "- Alex (person): Alex likes tea")


class PlainFactLines(unittest.TestCase):
    def test_matching_facts_come_first_then_the_newest_in_their_saved_order(self):
        facts = ["Sam is a vet", "Likes tea", "Owns a cat", "Plays chess"]
        budget = len("- Sam is a vet\n- Plays chess")
        self.assertEqual(memory_recall.fact_lines(facts, "who is Sam?", budget), "- Sam is a vet\n- Plays chess")
        self.assertEqual(memory_recall.fact_lines(facts, "", 1000), "- Sam is a vet\n- Likes tea\n- Owns a cat\n- Plays chess")
        self.assertEqual(memory_recall.fact_lines([], "tea", 1000), "")


class BrainAsksForTheRequest(unittest.TestCase):
    def test_current_context_recalls_with_the_newest_request(self):
        with unittest.mock.patch.object(brain, "http_json", return_value=big_graph()), \
                unittest.mock.patch.object(brain, "recent_exchanges", return_value=""):
            context = brain.current_context({"time_zone": "Europe/London"}, "what does my sister Sam do?")
        self.assertIn("works as a vet in Leeds", context)

    def test_the_hub_being_down_means_no_memories(self):
        with unittest.mock.patch.object(brain, "http_json", side_effect=TimeoutError("slow")):
            self.assertEqual(brain.recalled_memories("tea"), "")


if __name__ == "__main__":
    unittest.main()

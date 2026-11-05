import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "assistant"))

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import quick_answers

EVENING = datetime(2026, 11, 5, 17, 44)
MORNING = datetime(2026, 11, 5, 0, 5)
EPHEMERAL = [{"word": "ephemeral", "meanings": [
    {"partOfSpeech": "adjective", "definitions": [{"definition": "Lasting for a short period of time."}]},
    {"partOfSpeech": "noun", "definitions": [{"definition": "Something which lasts for a short period of time."}]},
    {"partOfSpeech": "verb", "definitions": [{"definition": "Never spoken."}]},
]}]


@pytest.mark.parametrize("question", ["What time is it?", "what's the time", "Arty, what is the time right now?",
                                      "can you tell me the time please", "time"])
def test_time_questions_are_answered_from_the_clock(question):
    assert quick_answers.time_answer(question, EVENING) == "It's 5:44 pm."


def test_just_after_midnight_reads_as_twelve_am():
    assert quick_answers.time_answer("what time is it", MORNING) == "It's 12:05 am."


def test_date_and_day_questions():
    assert quick_answers.time_answer("what's the date today?", EVENING) == "It's Thursday 5 November 2026."
    assert quick_answers.time_answer("what day is it", EVENING) == "It's Thursday."


@pytest.mark.parametrize("question", ["what time does the shop close?", "is it time for bed", "how long until bedtime"])
def test_other_questions_about_time_go_to_the_brain(question):
    assert quick_answers.time_answer(question, EVENING) is None


@pytest.mark.parametrize("question, term", [
    ("What does ephemeral mean?", "ephemeral"),
    ("what's the meaning of 'serendipity'", "serendipity"),
    ("define the word ubiquitous", "ubiquitous"),
    ("Arty, what is the definition of red herring?", "red herring"),
    ("meaning of petrichor", "petrichor"),
])
def test_word_questions_name_the_word(question, term):
    assert quick_answers.word_to_define(question) == term


@pytest.mark.parametrize("question", ["what does the fox say about climate change in the news today",
                                      "what does it all mean for the weather", "what is ephemeral"])
def test_long_or_unclear_word_questions_are_left_for_the_brain(question):
    assert quick_answers.word_to_define(question) is None


def test_a_definition_is_spoken_as_at_most_two_meanings():
    spoken = quick_answers.spoken_definition("ephemeral", EPHEMERAL)
    assert spoken == ("Ephemeral. As an adjective: Lasting for a short period of time. "
                      "As a noun: Something which lasts for a short period of time.")


@pytest.mark.parametrize("entries", [[], {"title": "No Definitions Found"}, [{"meanings": []}], "nonsense"])
def test_missing_definitions_give_nothing(entries):
    assert quick_answers.spoken_definition("qwzxv", entries) is None


@pytest.mark.parametrize("request_text, query", [
    ("search up the opening times for Tesco", "the opening times for Tesco"),
    ("can you look up who won the match last night?", "who won the match last night"),
    ("Google cheap flights to Spain", "cheap flights to Spain"),
    ("search the web for Quest 3S battery life", "Quest 3S battery life"),
])
def test_search_requests_keep_what_to_search_for(request_text, query):
    assert quick_answers.search_query(request_text) == query


def test_a_search_is_asked_of_the_brain_as_a_web_search():
    assert quick_answers.search_question("Tesco hours") == "Search the web for Tesco hours and tell me what you find."


def look_up(handler, term):
    async def run():
        app = web.Application()
        app.router.add_get("/api/v2/entries/en/{term}", handler)
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()
        original = quick_answers.DICTIONARY_URL
        quick_answers.DICTIONARY_URL = str(server.make_url("/api/v2/entries/en/"))
        try:
            return await quick_answers.look_up_word(client.session, term)
        finally:
            quick_answers.DICTIONARY_URL = original
            await client.close()

    return asyncio.run(run())


def test_a_word_is_looked_up_in_the_dictionary():
    async def found(request):
        assert request.match_info["term"] == "ephemeral"
        return web.json_response(EPHEMERAL)

    assert look_up(found, "ephemeral").startswith("Ephemeral. As an adjective")


def test_an_unknown_word_or_a_broken_dictionary_gives_nothing():
    async def missing(request):
        return web.json_response({"title": "No Definitions Found"}, status=404)

    async def broken(request):
        return web.Response(text="<html>oops</html>", content_type="text/html")

    assert look_up(missing, "qwzxv") is None
    assert look_up(broken, "qwzxv") is None

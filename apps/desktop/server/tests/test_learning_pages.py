import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from process import browser, own_browser, quiz
from process.tools import computer, registry

LEARNING_PAGE = {"url": "https://lms.college.example/course/7/quiz", "title": "Week 4 Quiz",
                 "text": "Question 1. Which layer does TCP work at?",
                 "items": [{"n": 3, "kind": "button", "label": "Transport"}, {"n": 4, "kind": "button", "label": "Network"}]}
TRIVIA_PAGE = {"url": "https://trivia.example/q", "title": "Capitals quiz", "text": "What is the capital of Peru?",
               "items": [{"n": 7, "kind": "button", "label": "Lima"}]}


@pytest.mark.parametrize("url, title, text", [
    ("https://www.testdome.com/tests/python/1", "Python test", "Question"),
    ("https://app.coderpad.io/ABC123", "Interview", "Write a function"),
    ("https://form.typeform.com/to/abc", "Product knowledge quiz", "Your score is shown at the end"),
    ("https://www.surveymonkey.com/r/xyz", "Safety quiz", "Pick the correct answer. 2 points"),
    ("https://b.socrative.com/student/", "Room", "Answer"),
    ("https://jobs.example/apply/step3", "Numerical aptitude test", "Question 1"),
    ("https://study.example/q", "Quiz", "This quiz counts towards your final mark."),
    ("https://careers.example/screening", "Candidate screening", "Question 4 of 20"),
    ("https://anything.example/x", "Week 6 Quiz", "")],
    ids=["testdome", "coderpad", "typeform quiz", "surveymonkey quiz", "socrative", "aptitude test", "counts toward mark",
         "question n of m on a hiring page", "week n quiz"])
def test_more_hiring_tests_and_graded_quizzes_count_as_learning_pages(url, title, text):
    assert quiz.is_learning_page(url, title, text)


@pytest.mark.parametrize("url, title, text", [
    ("https://form.typeform.com/to/abc", "Book a table", "Your name and the time you want"),
    ("https://www.sporcle.com/games/x", "Quiz", "Question 3 of 10. Which planet is largest?")])
def test_ordinary_forms_and_trivia_are_not_learning_pages(url, title, text):
    assert not quiz.is_learning_page(url, title, text)


@pytest.mark.parametrize("url, title, text", [
    ("https://canvas.instructure.com/courses/1/assignments/9", "Essay 2: causes of the First World War",
     "Assignment. Points possible: 20. Submit assignment"),
    ("https://moodle.school.example/mod/assign/view.php", "Coursework submission", "Upload your coursework here."),
    ("https://homework.example/sheet/3", "Homework 3", "Maths homework: questions 1 to 10 on integration by parts."),
    ("https://www.khanacademy.org/math/algebra/lesson-3", "Lesson 3: linear equations", "Year 9. Module 2. Grade 8."),
    ("https://app.cyberbitrange.com/lab/3", "Lab", ""),
    ("https://docs.google.com/forms/d/e/abc/viewform", "Book club sign-up", "Your name and email"),
    ("https://study.example/notes", "Graded assignment 4", "This assignment counts towards your final grade.")],
    ids=["canvas assignment", "moodle coursework upload", "homework sheet", "lesson page", "cyber range lab",
         "google form sign-up", "graded assignment"])
def test_homework_coursework_and_assignment_pages_are_not_held(url, title, text):
    assert not quiz.is_learning_page(url, title, text)


@pytest.mark.parametrize("url, title, text", [
    ("https://moodle.school.example/mod/quiz/attempt.php", "Homework quiz", "Assignment 3 questions"),
    ("https://canvas.instructure.com/courses/1/quizzes/2/take", "Coursework check", "Question 1"),
    ("https://study.example/paper", "Final exam", "Your coursework mark is added later."),
    ("https://careers.example/assessment", "Online assessment", "Homework-style task. Question 2 of 15 for candidates."),
    ("https://www.hackerrank.com/test/abc123", "Take-home assignment", "Write a function")],
    ids=["moodle quiz", "canvas quiz", "exam", "hiring assessment", "hiring test site"])
def test_live_quizzes_exams_and_hiring_tests_are_held_even_when_they_mention_coursework(url, title, text):
    assert quiz.is_learning_page(url, title, text)


@pytest.mark.parametrize("url, title, text", [
    ("https://mylab.pearson.com/Student/Player.aspx?id=5", "MyLab Math", "Question 1 of 20 This question: 1 pt"),
    ("https://vle.uni.example/mod/hvp/view.php?id=77", "Week 3 knowledge check",
     "Question 1 Not yet answered Marked out of 1.00 Flag question Select one: a. b. c. Next page"),
    ("https://moodle.uni.example/mod/page/view.php?id=8", "Week 3", "Attempt 2. Finish attempt ..."),
    ("https://www.netacad.com/launch?id=abc", "CCNA 1 Final", "Question 1 of 60 Which command? Submit"),
    ("https://docs.google.com/forms/d/e/abc/viewform", "Year 9 Biology end of unit",
     "Name * What is the powerhouse of the cell? * Mitochondria Nucleus Submit")],
    ids=["mylab 1 pt", "moodle marked out of", "moodle finish attempt", "netacad question n of m",
         "google form end of unit"])
def test_live_tests_with_quieter_signs_are_held(url, title, text):
    assert quiz.is_learning_page(url, title, text)


@pytest.mark.parametrize("url, title, text", [
    ("https://tryhackme.com/room/linuxfundamentals", "Linux Fundamentals", "Task 1 Answer the questions below Submit"),
    ("https://moodle.uni.example/mod/assign/view.php?id=5", "Essay 1", "Submission status Due date Add submission"),
    ("https://docs.google.com/forms/d/e/abc/viewform", "Club sign-up", "Your name, your year group and a time")],
    ids=["tryhackme room", "moodle essay", "google form sign-up"])
def test_the_wider_signs_still_leave_coursework_alone(url, title, text):
    assert not quiz.is_learning_page(url, title, text)


class FakeChrome:
    def __init__(self, page):
        self.page = page

    def __call__(self, action, **arguments):
        return {**self.page, "tab": 1}


@pytest.fixture
def reading(monkeypatch):
    monkeypatch.setattr(browser, "site_allowed", lambda site: True)
    monkeypatch.setattr(browser, "connected", lambda: True)
    monkeypatch.setattr(computer, "available", lambda: True)
    monkeypatch.setattr(registry, "_allowed", lambda key: True)
    monkeypatch.setattr(own_browser, "available", lambda: True)
    pages = {"chrome": LEARNING_PAGE, "own": TRIVIA_PAGE}
    monkeypatch.setattr(browser, "_ask", lambda action, **arguments: {**pages["chrome"], "tab": 1})
    monkeypatch.setattr(own_browser._browser, "read", lambda: pages["own"])
    monkeypatch.setattr(own_browser._browser, "click", lambda item, text, expected, acts: {"clicked": expected, "url": pages["own"]["url"], "title": ""})
    monkeypatch.setitem(registry._HANDLERS, "computer_click", (lambda **args: "clicked", lambda: True))
    quiz.forget_choices()
    registry.start_reply("help me with this")
    yield pages
    quiz.learning_pages.forget()


def clicking_is_held_back(name, arguments):
    held = registry.needs_ok(name, json.dumps(arguments), {"gui"}) is None and \
        registry.run(name, json.dumps(arguments)) == quiz.NOT_AFTER_A_LEARNING_PAGE
    return held


def test_a_course_page_found_by_an_ordinary_read_holds_back_answer_clicks(reading):
    registry.run("chrome_read", "{}")
    assert clicking_is_held_back("chrome_click", {"item": 3})
    assert clicking_is_held_back("chrome_type", {"text": "Transport", "label": "answer"})
    assert clicking_is_held_back("computer_click", {"target": "Transport"})


def test_the_hold_lasts_into_the_next_reply_and_then_ends(reading):
    registry.run("chrome_read", "{}")
    registry.start_reply("and the next one?")
    assert clicking_is_held_back("chrome_click", {"item": 3})
    registry.start_reply("now something else")
    assert not quiz.learning_pages.holds_any()


def test_another_site_read_in_arty_s_own_browser_is_not_held_back_in_the_next_reply(reading):
    registry.run("chrome_read", "{}")
    registry.start_reply("now the trivia one")
    registry.run("arty_browser_read", "{}")
    assert registry.run("arty_browser_click", json.dumps({"item": 7})) != quiz.NOT_AFTER_A_LEARNING_PAGE


def test_a_course_page_in_arty_s_own_browser_holds_back_its_clicks(reading):
    reading["own"] = LEARNING_PAGE
    registry.run("arty_browser_read", "{}")
    assert clicking_is_held_back("arty_browser_click", {"item": 3})


HOMEWORK_PAGE = {"url": "https://canvas.instructure.com/courses/1/assignments/9", "title": "Homework 3: photosynthesis",
                 "text": "Assignment. Write 500 words on photosynthesis. Points possible: 20.",
                 "items": [{"n": 5, "kind": "textbox", "label": "Your answer"}]}


def test_a_homework_page_read_in_their_browser_does_not_hold_back_clicking_or_typing(reading):
    reading["chrome"] = HOMEWORK_PAGE
    registry.run("chrome_read", "{}")
    assert not quiz.learning_pages.holds_any()
    assert registry.run("computer_click", json.dumps({"target": "Your answer"})) == "clicked"

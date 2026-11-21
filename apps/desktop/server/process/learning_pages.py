import re
from urllib.parse import urlsplit

PAGE_TEXT_CHARS = 6000
REPLIES_HELD = 2
SCREEN = "the screen"
TEST_SITES = re.compile(
    r"examity|proctor|pearsonvue|certif|comptia|isc2\.org|hackerrank|codility|testgorilla|codesignal|hirevue|"
    r"testdome|coderpad|socrative|kahoot", re.I)
LEARNING_SITES = re.compile(
    r"moodle|instructure\.com|canvas\.|blackboard|brightspace|d2l\.|schoology|classroom\.google|coursera|edx\.org|"
    r"udemy|khanacademy|pluralsight|linkedin\.com/learning|codecademy|quizlet|pearson|mheducation|cengage|chegg|"
    r"tryhackme|hackthebox|cyberbit|duolingo|sololearn|brilliant\.org|netacad|skillsforall|testout|vle\.|lms\.|"
    r"elearning|e-learning|\.ac\.uk|\.edu\b|\.sch\.uk|\.k12\.|gradescope", re.I)
QUIZ_BUILDER_SITES = re.compile(
    r"typeform\.com|surveymonkey\.|research\.net|docs\.google\.com/forms|forms\.gle|forms\.office\.com|"
    r"forms\.microsoft\.com", re.I)
QUIZ_BUILDER_WORDS = re.compile(r"\b(quiz|score|scored|points?|correct answers?|marks?|test|exam|"
                                r"end of (term|year|unit|topic))\b", re.I)
TEST_NAMES = re.compile(r"(?<![a-z])(quiz|quizzes|quizzing|tests?|exams?|assessments?)(?![a-z])", re.I)
LIVE_TEST_SIGNS = re.compile(r"\b(time (left|remaining)|time limit|timer|\d+ (pts?|points?|marks?)|quiz \d+|test \d+|"
                             r"submit (all|answers)|quiz instructions|marked out of|question \d+ (of|/) \d+|"
                             r"attempt \d+|not yet answered|flag question|finish attempt)\b", re.I)
TEST_WORDS = re.compile(
    r"\b(exam|examination|assessment|certification|unit test|midterm|final test|proctored|attempt \d+ of|"
    r"week \d+ (quiz|test)|chapter \d+ (quiz|test)|end of (term|year|unit|topic) (quiz|test)|"
    r"submit (your )?(quiz|test|assessment)|aptitude test|psychometric test)\b", re.I)
GRADED_WORDS = re.compile(
    r"\b(graded|your grade|points possible|marks? available|"
    r"counts? towards? (your )?(final |overall )?(mark|grade|score|result))\b", re.I)
QUESTION_COUNT = re.compile(r"\bquestion \d+ (of|/) \d+\b", re.I)
TEST_CONTEXT = re.compile(r"\b(test|candidate|applicant|assessment|screening|interview|recruit\w*|hiring)\b", re.I)

_held = {}
_last_site = {}
_this_reply = {"found": False}


def _host_and_path(url):
    parts = urlsplit(str(url or ""))
    return f"{parts.hostname or ''}{parts.path or ''}"


def site_of(url):
    return (urlsplit(str(url or "")).hostname or "").lower()


def is_graded_test(words):
    return bool(TEST_WORDS.search(words) or (GRADED_WORDS.search(words) and TEST_NAMES.search(words)))


def is_test_on_a_learning_site(place, title, words):
    return bool(TEST_NAMES.search(place) or TEST_NAMES.search(str(title or "")) or LIVE_TEST_SIGNS.search(words))


def is_learning_page(url, title="", text=""):
    place = _host_and_path(url)
    words = f"{title}\n{str(text or '')[:PAGE_TEXT_CHARS]}"
    if TEST_SITES.search(place) or is_graded_test(words):
        return True
    if LEARNING_SITES.search(place) and is_test_on_a_learning_site(place, title, words):
        return True
    if QUIZ_BUILDER_SITES.search(place) and QUIZ_BUILDER_WORDS.search(words):
        return True
    return bool(QUESTION_COUNT.search(words) and TEST_CONTEXT.search(words))


def note_page(url, title="", text="", where=SCREEN):
    site = site_of(url) or SCREEN
    _last_site[where] = site
    if is_learning_page(url, title, text):
        _held[site] = REPLIES_HELD
        _this_reply["found"] = True
        return True
    return False


def new_reply():
    _this_reply["found"] = False
    for site in list(_held):
        _held[site] -= 1
        if _held[site] <= 0:
            del _held[site]


def holds_any():
    return bool(_held)


def holds(where):
    return _this_reply["found"] or _last_site.get(where) in _held


def forget():
    _this_reply["found"] = False
    _held.clear()
    _last_site.clear()

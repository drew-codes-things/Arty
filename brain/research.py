"""Desktop research: "use your desktop to find out ..." gets a full, sourced answer.

A small model can't reliably drive a browser through a dozen steps, so the steps are fixed here and the
model only reads and writes: search the web, open the best few results in the browser on Arty's desktop
(the user can watch it), note what each page says about the question (one page at a time, so a
long page fits the model's context), then write one answer from all the notes, citing them. The source
list is added by this code, so the links are always the real ones. Pages showing a bot check are skipped,
never got round.
"""
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as PageTimeout
from datetime import datetime
from urllib.parse import urlparse

import quick

PAGES = 4
PAGE_CHARS = 5000         # enough for the substance of a page; more only slows the model
MIN_PAGE_CHARS = 400      # less than this is a cookie wall, an error page or a bot check
SEARCH_RESULTS = 8
NOTHING = "NOTHING RELEVANT"
ENOUGH_SOURCES = 2
PAGE_SECONDS = 25
FACT_QUESTION = re.compile(r"^\W*(?:who|when|where|what year|which year|what date|how (?:long|much|many|old|far|big|tall|heavy|hot)|"
                           r"(?:what|which) (?:is|was|are|were) the (?:name|date|price|cost|population|capital|height|length|"
                           r"distance|weight|temperature|time))\b", re.IGNORECASE)

# "use your desktop to find out ...", "on your computer, look up ...", "research this properly".
REQUEST = re.compile(
    r"\b(use|using|on|with) your (desktop|computer|pc|browser)\b.*?\b(search|look(?: it| that| this)? up|find(?: out)?|"
    r"research|google|look into|check (?:out|what|how|why|if|whether)|tell me|what|how|why|which|who|when|best)\b"
    r"|\b(deep|proper|full|thorough) research\b|\bresearch (this|that|it) (properly|in depth|fully)\b",
    re.IGNORECASE | re.DOTALL)
# The asking itself, which only makes a search query worse.
ASKING = re.compile(
    r"^\s*(hey |hi )?(arty,? )?((can|could|would) you |please )*(use|using|on|with) your (desktop|computer|pc|browser),?\s*(to |and )?"
    r"|^\s*((can|could|would) you |please )*(do (a |some )?)?(deep|proper|full|thorough) research (on|into|about)?\s*"
    r"|\b(and |then )?(tell me|let me know|report back|come back)( what you find| to me)?\.?\s*$",
    re.IGNORECASE)
LEADING_VERB = re.compile(r"^(search( the web)?( for)?|look up|find( out)?|research|google|look into)\s+", re.IGNORECASE)
RESULT_URL = re.compile(r"^\s*\d+\.\s+.*\n\s+(https?://\S+)", re.MULTILINE)
SKIPPED_SITES = ("youtube.com", "youtu.be", "tiktok.com", "instagram.com", "facebook.com", "x.com", "twitter.com")
BOT_CHECK = re.compile(r"verify you are (a )?human|are you a robot|unusual traffic|captcha|checking your browser|enable javascript and cookies",
                       re.IGNORECASE)

PAGE_PROMPT = """Question: {question}

Below is the text of a web page ({url}). Write notes on everything in it that helps answer the question: facts,
steps, numbers, names, recommendations and reasons, pros and cons, warnings. Be specific and keep the details;
use short bullet points. If nothing on the page helps, reply only: {nothing}

Page text:
{text}"""

# The premise check and the answer share everything up to the end of the notes, so the model's cache of that
# (most of the prompt) is reused and the check costs only its short reply.
NOTES_PROMPT = """Question: {question}

You researched this on the web. Here are your notes from each source, numbered:

{notes}

"""
PREMISE_PROMPT = NOTES_PROMPT + """Today is {today}. Before answering, check the notes against the question. Reply with exactly one of these:
WRONG: <one sentence correcting it>  if the notes show the question takes for granted something false (a wrong
place, date, person, team, number, or an event that never happened);
MISSING  if the notes don't contain the answer to the question;
FINE  if the notes answer the question and its assumptions are right.
Only say WRONG when the notes clearly contradict a specific detail written in the question; a vague question is FINE.
Reply with WRONG, MISSING or FINE first."""
CORRECTION_PROMPT = NOTES_PROMPT + """The question gets a detail wrong, according to the notes. In one sentence, say what's true and
what the question got wrong, in this form: "It was <what the notes say>, not <the detail in the question>."."""
# The question's subject alone, for a second search when the first found nothing that answers it ("1923 FA Cup
# final" for "the attendance at the 1923 FA Cup final replay in Dudley"): a wrong detail in the question can
# send the search to the wrong pages, and the subject's own page shows the detail is wrong.
SUBJECT_PROMPT = """Question: {question}

What is this question about? Reply with only a short web search for its main subject (2 to 6 words), leaving out
the details it asks about."""
NOT_FOUND = ("I couldn't find a reliable answer to that: the sources I found don't cover {query}, so I won't guess. "
             "If you know where it's from (a site, a book, a different year or place), tell me and I'll look again.")
CORRECTION_NOTE = ("The question gets something wrong. {correction} Start your answer by saying so plainly, then "
                   "answer what the user most likely wants to know.\n\n")
ANSWER_PROMPT = NOTES_PROMPT + """{correction}Today is {today}: a fact dated before today in the notes is still the latest
you know, so give it as the current answer with its date. Write the answer for the user in Markdown, as one expert answer: merge what the sources say, never source by
source. Open with the direct answer in one or two sentences, with no heading or title above it. If the question
is a simple fact (who, when, how much), that's nearly all: add a sentence or two of useful detail and stop. If it
asks how to do something or what's best, follow with the detail: steps, options, specifics (numbers, names,
times, costs), recommendations and why, and warnings, using short headings named after what's under them only
if the answer is long. Where sources disagree, say how. Put the source numbers like [1] or [2] after the facts
that come from them. Use only what's in the notes: if they don't answer the question, say you couldn't find it,
and never guess a number, date or name. Don't write a sources section; it's added for you."""

QUICK_PAGE_CHARS = 3000   # quick questions: short excerpts, read in one go

# Headings that only repeat the instructions, and a sources section the model wrote anyway.
INSTRUCTION_HEADING = re.compile(r"^#{1,6}\s*\**(direct answer|full detail|answer|details?)\**:?\s*\**\s*$\n?", re.IGNORECASE | re.MULTILINE)
LEADING_TITLE = re.compile(r"^\s*#{1,6}[^\n]*\n+")
OWN_SOURCES = re.compile(r"\n+(#{1,6}\s*)?\**sources?\**:?\**\s*(\n|$)[\s\S]*$", re.IGNORECASE)

log = logging.getLogger("brain.research")


# Questions the web answers better than a small model's memory: recent facts, "best", "how do I make".
QUICK_QUESTION = re.compile(
    r"\b(best|top \d+|recommend(ed|ation)?s?|recipes?|how (do|can|should) i (make|cook|bake|fix|build|clean|grow)|"
    r"how to (make|cook|bake|fix|build|clean|grow)|reviews?|latest|news|price of|cheapest|worth (it|buying)|who won|"
    r"who is the (current|new)|current(ly)?|this (year|season|week'?s)|last (year|season|world cup|election|game|match)|"
    r"20[2-3]\d|1[5-9]\d\d|what year|in which year)\b"
    r"|\b(?:interest|base|bank|exchange|inflation|mortgage|savings|unemployment|tax|vat) rates?\b"
    r"|\b(?:share|stock|oil|petrol|diesel|fuel|gas|electricity|energy|gold|silver|bitcoin|house) prices?\b"
    r"|\bhow much (?:is|are|does|do)\b[^.?!]{1,40}\b(?:worth|cost)\b"
    r"|\b(?:rates?|prices?|costs?|yields?|inflation|value)\b[^.?!]{0,40}\b(?:right now|now|at the moment|today)\b",
    re.IGNORECASE)
# About the user's own things ("my latest email"): not for the web, whatever the words.
ABOUT_THEIR_OWN = re.compile(r"\b(my|our|i'?m|i am|i'?ve|i have|i was)\b", re.IGNORECASE)
POLITE_ASKING = re.compile(r"\b(can|could|would|will|do) you\b|\byou think\b", re.IGNORECASE)
ABOUT_ARTY = re.compile(r"\b(you|your|yourself)\b", re.IGNORECASE)
LEANS_ON_EARLIER = re.compile(r"\b(?:that|(?<!worth )it|this(?! (?:year|season|week))|here|the (?:\w+ )?one|what about)\b|"
                              r"^\W*(?:and|so)\b|^\W*what (?:do|would|could) you recommend\W*$", re.IGNORECASE)
QUICK_PAGES = 2
OPEN_A_PAGE = re.compile(r"\b(open|go to|visit|load|navigate to)\s+(https?://)?[\w-]+(\.[\w-]+)+", re.IGNORECASE)
SNIPPET_CHARS = 1500


def is_request(text: str) -> bool:
    return bool(REQUEST.search(text or "")) and not OPEN_A_PAGE.search(text or "")


def is_quick_question(text: str) -> bool:
    text = text or ""
    if not QUICK_QUESTION.search(text) or ABOUT_THEIR_OWN.search(text) or quick.refers_back(text) or LEANS_ON_EARLIER.search(text):
        return False
    return not ABOUT_ARTY.search(POLITE_ASKING.sub("", text))


def query_from(text: str) -> str:
    """The search query in the user's words, without the asking ("can you use your desktop to ...")."""
    query = text
    for _ in range(3):  # the patterns can uncover one another
        query = LEADING_VERB.sub("", ASKING.sub("", query).strip())
    return query.strip(" ?.!,") or text.strip()


QUERY_WORD = re.compile(r"[a-z0-9]{3,}")
STOP_WORDS = {"the", "and", "for", "what", "who", "how", "was", "were", "are", "with", "that", "this", "from", "last", "best", "way"}


def relevant_excerpt(text: str, question: str, limit: int) -> str:
    """The parts of a page that match the question, in page order, up to limit characters: the start of a long
    page ("List of World Cup finals") is often history, and the answer further down."""
    if len(text) <= limit:
        return text
    words = set(QUERY_WORD.findall(question.lower())) - STOP_WORDS
    years = set(re.findall(r"\b20[2-3]\d\b", text))
    newest = max(years) if years else None
    blocks = [block for block in re.split(r"\n\s*\n|\n(?=[#*-] )", text) if block.strip()]

    def score(block):
        lowered = block.lower()
        return sum(lowered.count(word) for word in words) + (3 if newest and newest in block else 0)

    ranked = sorted(range(len(blocks)), key=lambda index: -score(blocks[index]))
    chosen, used = set(), 0
    for index in ranked:
        if used + len(blocks[index]) > limit:
            continue
        chosen.add(index)
        used += len(blocks[index])
    return "\n\n".join(blocks[index] for index in sorted(chosen)) or text[:limit]


def result_urls(search_text: str) -> list[str]:
    """The result links from web_search's text, one per site, skipping video and social sites."""
    urls, sites = [], set()
    for url in RESULT_URL.findall(search_text):
        site = urlparse(url).netloc.lower().removeprefix("www.")
        if site and site not in sites and not any(site == s or site.endswith("." + s) for s in SKIPPED_SITES):
            sites.add(site)
            urls.append(url)
    return urls


def page_text(browser_output: str) -> str:
    """The readable text from browser_open's output (which also lists clickable items)."""
    marker = browser_output.find("Page text:")
    return (browser_output[marker + len("Page text:"):] if marker >= 0 else browser_output).strip()


def read_page(url: str, call_tool, browser: bool = True) -> str:
    """The page's text: from the browser on Arty's desktop, or fetched plainly (quicker, or if the browser can't)."""
    text = ""
    try:
        text = page_text(call_tool("workspace__browser_open", {"url": url})) if browser else ""
    except Exception as error:  # the desktop may be off: fall back to a plain fetch
        log.warning("browser couldn't open %s: %s", url, error)
    if len(text) < MIN_PAGE_CHARS or BOT_CHECK.search(text[:1500]):
        try:
            text = call_tool("fetch__fetch", {"url": url, "max_length": PAGE_CHARS})
        except Exception as error:
            log.warning("couldn't fetch %s: %s", url, error)
            return ""
    if len(text) < MIN_PAGE_CHARS or BOT_CHECK.search(text[:1500]):
        return ""  # a bot check or an empty page: skipped, never got round
    return text[:PAGE_CHARS]


VERDICT = re.compile(r"^\W*(WRONG|MISSING|FINE)\b\W*(.*)", re.IGNORECASE | re.DOTALL)
# "FINE. The notes do not contain any information about ...": small models say one thing and mean another.
SAYS_MISSING = re.compile(r"\b(do(es)? not|don't|doesn't) (contain|mention|include|say|cover|provide)|\bno (information|mention|details)\b",
                          re.IGNORECASE)
CITATION = re.compile(r"\s?\[(\d+)\]")
# The part of a correction that says what's false ("..., not Dudley", "there was no replay").
NEGATED = re.compile(r"\b(?:not|no|never|wasn't|weren't|didn't|isn't|aren't|rather than|instead of)\b(.*)", re.IGNORECASE | re.DOTALL)
# Words too general to show a correction is about the question ("not in a single year").
GENERAL_WORDS = STOP_WORDS | {"year", "years", "date", "dates", "time", "day", "days", "single", "exact", "exactly", "one", "any",
                              "all", "only", "just", "actually", "really", "question", "information", "notes", "specific"}


def _words(text: str) -> set[str]:
    return {word.rstrip("s") for word in QUERY_WORD.findall(text.lower())} - {word.rstrip("s") for word in GENERAL_WORDS}


RESULT_START = re.compile(r"^\d+\.\s+", re.MULTILINE)
SEARCH_FAILED = ("search is unavailable", "No results")
NO_RELEVANT_RESULTS = "No results about: {question} (the search only found pages about other things)"
ASKING_WORDS = {"use", "using", "show", "code", "example", "get", "make", "does", "did", "can", "could", "would", "should",
                "will", "you", "your", "there", "their", "into", "about", "which", "when", "where", "why", "right", "now",
                "today", "tell", "give", "explain", "need", "want", "know", "have", "has", "had", "work", "help", "run",
                "build", "built", "write", "take", "find", "look", "like", "new", "fix", "some", "still", "out", "desktop"}
FEW_KEY_WORDS = 2
SOME_KEY_WORDS = 6
SHARED_WORDS_FOR_SOME = 2
SHARED_WORDS_FOR_MANY = 3


def key_words(question: str) -> set[str]:
    return _words(question) - {word.rstrip("s") for word in ASKING_WORDS}


def shared_words_needed(words: set[str]) -> int:
    if len(words) <= FEW_KEY_WORDS:
        return min(1, len(words))
    return SHARED_WORDS_FOR_SOME if len(words) <= SOME_KEY_WORDS else SHARED_WORDS_FOR_MANY


def is_about(text: str, words: set[str]) -> bool:
    return len(words & _words(text)) >= shared_words_needed(words)


def found_nothing(search_text: str) -> bool:
    return search_text.startswith(SEARCH_FAILED)


def relevant_results(search_text: str, question: str) -> str:
    starts = [match.start() for match in RESULT_START.finditer(search_text)]
    words = key_words(question)
    if found_nothing(search_text) or not starts or not words:
        return search_text
    header = search_text[:starts[0]]
    entries = [search_text[start:end] for start, end in zip(starts, starts[1:] + [len(search_text)])]
    kept = [RESULT_START.sub("", entry, count=1) for entry in entries if is_about(entry, words)]
    if not kept:
        return NO_RELEVANT_RESULTS.format(question=question.strip())
    numbered = [f"{number}. {entry.rstrip()}" for number, entry in enumerate(kept, start=1)]
    return (header if is_about(header, words) else "") + "\n".join(numbered)


def contradicts_question(correction: str, question: str) -> bool:
    """Whether the correction says something written in the question is false: its "not ..." part names it."""
    negated = NEGATED.search(correction)
    return bool(negated and _words(negated.group(1)) & _words(question))


def check_premise(question: str, notes: str, complete) -> tuple[str, str]:
    """("wrong", the correction), ("missing", "") when the notes don't answer the question, or ("fine", "")."""
    reply = complete(PREMISE_PROMPT.format(question=question, notes=notes, today=today_text())).strip()
    found = VERDICT.match(reply)
    verdict, rest = (found.group(1).lower(), found.group(2).strip()) if found else ("fine", reply)
    if verdict == "wrong":
        correction = rest.split("\n")[0].strip(" :")
        if not correction:  # "WRONG" and nothing more: ask what (the notes are cached, so it's quick)
            correction = complete(CORRECTION_PROMPT.format(question=question, notes=notes)).strip().split("\n")[0].strip(' "')
        if contradicts_question(correction, question):
            log.info("question has a false premise: %s", correction[:160])
            return "wrong", correction
        log.info("ignored a correction that doesn't contradict the question: %s", correction[:160])
        return "fine", ""
    if verdict == "fine" and SAYS_MISSING.search(rest[:300]):
        verdict = "missing"
    return verdict, ""


def subject_of(question: str, complete) -> str:
    subject = complete(SUBJECT_PROMPT.format(question=question)).strip().splitlines()
    subject = (subject[0] if subject else "").strip(" \"'.?")
    return subject if 0 < len(subject) <= 80 and subject.lower() != question.lower() else ""


def keep_real_citations(answer: str, sources: int) -> str:
    """Drops citation marks that point at no source (a small model numbers past the list)."""
    return CITATION.sub(lambda mark: mark.group(0) if 1 <= int(mark.group(1)) <= sources else "", answer)


def today_text() -> str:
    return datetime.now().strftime("%A %-d %B %Y")


def asks_a_plain_fact(query: str) -> bool:
    return bool(FACT_QUESTION.search(query))


BROWSER = threading.Lock()


class PageReader:
    def __init__(self, urls: list[str], call_tool, browser: bool):
        self.urls, self.call_tool, self.browser = urls, call_tool, browser
        self.opener = ThreadPoolExecutor(max_workers=1, thread_name_prefix="research-pages")
        self.ahead = {}
        self.closed = threading.Event()

    def open_in_browser(self, url: str) -> str:
        with BROWSER:
            if self.closed.is_set():
                return ""
            return read_page(url, self.call_tool, True)

    def open_ahead(self, index: int):
        if self.browser and index < len(self.urls) and index not in self.ahead:
            self.ahead[index] = self.opener.submit(self.open_in_browser, self.urls[index])

    def text(self, index: int, look_ahead: bool) -> str:
        url = self.urls[index]
        if not self.browser:
            return read_page(url, self.call_tool, False)
        self.open_ahead(index)
        try:
            text = self.ahead.pop(index).result(timeout=PAGE_SECONDS)
        except PageTimeout:
            log.warning("the browser took over %s s to open %s; reading the rest without it", PAGE_SECONDS, url)
            self.browser = False
            return read_page(url, self.call_tool, False)
        if look_ahead:
            self.open_ahead(index + 1)
        return text

    def close(self):
        self.closed.set()
        self.opener.shutdown(wait=False, cancel_futures=True)


def gather(query: str, call_tool, complete, pages: int, browser: bool, quick_complete=None) -> tuple[str, list[str], list[str], str]:
    quick_complete = quick_complete or complete
    search = relevant_results(call_tool("research__web_search", {"query": query, "results": SEARCH_RESULTS}), query)
    sources, notes, verdict = [], [], ""
    reader = PageReader(result_urls(search), call_tool, browser)
    stops_early = browser and asks_a_plain_fact(query)
    try:
        for index, url in enumerate(reader.urls):
            if len(sources) >= pages:
                break
            text = reader.text(index, look_ahead=len(sources) + 1 < pages)
            if not text:
                continue
            if not browser:  # quick: the excerpt itself is the note, so there's one model call in all
                sources.append(url)
                notes.append(f"[{len(sources)}] ({url})\n{relevant_excerpt(text, query, QUICK_PAGE_CHARS)}")
                continue
            noted = quick_complete(PAGE_PROMPT.format(question=query, url=url, text=text, nothing=NOTHING)).strip()
            if not noted or NOTHING in noted[:len(NOTHING) + 5]:
                continue
            sources.append(url)
            notes.append(f"[{len(sources)}] ({url})\n{noted}")
            if stops_early and len(sources) >= ENOUGH_SOURCES and len(sources) < pages:
                checked, correction = check_premise(query, "\n\n".join(notes), quick_complete)
                if checked == "fine":
                    log.info("%d sources answer %r; not reading more", len(sources), query[:80])
                    verdict = checked
                    break
    finally:
        reader.close()
    if notes and not browser:  # the result snippets often hold the newest facts (a headline, a date)
        notes.append(f"(search result snippets)\n{search[:SNIPPET_CHARS]}")
    return search, sources, notes, verdict


def run(question: str, call_tool, complete, pages: int = PAGES, browser: bool = True, classify=None) -> tuple[str, list[str]]:
    """The researched answer and the tools used. call_tool(name, arguments) runs one of Arty's tools;
    complete(prompt) asks the model. browser=False reads pages with a plain fetch (quick questions).
    Before answering, the notes are checked against the question: a false premise is corrected first, and
    when nothing found answers it (even after searching for its subject alone) Arty says so rather than guess."""
    classify = classify or complete
    query = query_from(question)
    used = ["research__web_search"]
    search, sources, notes, verdict = gather(query, call_tool, complete, pages, browser, classify)
    if not notes:  # nothing readable: answer from the search results themselves
        if found_nothing(search):
            return f"I couldn't research that: {search}", used
        notes = [f"[1] (search results)\n{search}"]
    verdict, correction = (verdict, "") if verdict else check_premise(query, "\n\n".join(notes), classify)
    if verdict == "missing" and (subject := subject_of(query, complete)):
        log.info("nothing answered %r; searching for its subject: %s", query[:80], subject)
        _, more_sources, more_notes, _ = gather(subject, call_tool, complete, pages, browser, classify)
        if more_notes:
            sources, notes = more_sources, more_notes
            verdict, correction = check_premise(query, "\n\n".join(notes), complete)
    used += [("workspace__browser_open" if browser else "fetch__fetch")] if sources else []
    if verdict == "missing":
        return NOT_FOUND.format(query=query), used
    joined = "\n\n".join(notes)
    answer = complete(ANSWER_PROMPT.format(question=query, notes=joined, today=today_text(),
                                           correction=CORRECTION_NOTE.format(correction=correction) if correction else "")).strip()
    answer = LEADING_TITLE.sub("", OWN_SOURCES.sub("", INSTRUCTION_HEADING.sub("", answer)).strip()).strip()
    answer = keep_real_citations(answer, len(sources))
    if sources:
        answer += "\n\n**Sources**\n" + "\n".join(f"{number}. {url}" for number, url in enumerate(sources, 1))
    return answer, used

import json
import os
import socket
import sys
import threading
import time
import unittest
import unittest.mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_browser_requests import SHIPPED_CONFIG  # noqa: E402
from test_docs_lookup import DJANGO_PAGE, DJANGO_QUESTION, DJANGO_RESULTS, lookup_with  # noqa: E402

import docs_reading  # noqa: E402
import local_docs  # noqa: E402

TSTRING_QUESTION = ("How do I use Python 3.14 template strings (t-strings) to build an HTML snippet with the user's name "
                    "escaped? Show the code.")
WHATS_NEW_URL = "https://docs.python.org/3.14/whatsnew/3.14.html#whatsnew314-template-string-literals"
WHATS_NEW_HEADING = "What's new in Python 3.14 > New features > PEP 750: Template string literals"
WHATS_NEW_TEXT = """Template strings are a new mechanism for custom string processing.

```python
from string.templatelib import Interpolation

def lower_upper(template):
    parts = []
    for part in template:
        if isinstance(part, Interpolation):
            parts.append(str(part.value).upper())
        else:
            parts.append(part.lower())
    return ''.join(parts)

name = 'Wenslydale'
template = t'Mister {name}'
assert lower_upper(template) == 'mister WENSLYDALE'
```"""
PEP_URL = "https://peps.python.org/pep-0750/#motivation"
PEP_HEADING = "PEP 750: Template Strings > Motivation"
PEP_TEXT = "Template strings let a developer sanitise HTML:\n\n```python\ntemplate = t\"<p>{evil}</p>\"\n```"
DJANGO_LOCAL_URL = "https://docs.djangoproject.com/en/6.1/topics/tasks/#defining-tasks"


def found(*sections):
    return lambda question: [local_docs.Found(url, heading, text) for url, heading, text in sections]


class FakeLibrary(BaseHTTPRequestHandler):
    hits = []
    sections = {}
    asked = []

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        FakeLibrary.asked.append((parsed.path, query))
        if parsed.path == "/search":
            body = {"query": query["q"][0], "results": FakeLibrary.hits}
        else:
            body = FakeLibrary.sections[int(query["id"][0])]
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        pass


class TheLocalLibraryIsReadFirst(unittest.TestCase):
    def test_a_matching_local_section_is_used_and_the_web_page_is_not_fetched(self):
        fetched = []
        note = docs_reading.read_top_page(DJANGO_RESULTS, TSTRING_QUESTION, fetch=lambda url: fetched.append(url) or "",
                                          find_local=found((WHATS_NEW_URL, WHATS_NEW_HEADING, WHATS_NEW_TEXT)))
        self.assertEqual(fetched, [])
        self.assertIn(WHATS_NEW_URL, note)
        self.assertIn("def lower_upper(template):", note)
        self.assertIn("isinstance(part, Interpolation)", note)
        self.assertIn("local copy", note)
        self.assertIn("follow this page, not your memory", note)

    def test_a_second_section_is_added_when_it_fits(self):
        note = docs_reading.read_top_page("", TSTRING_QUESTION, fetch=lambda url: "",
                                          find_local=found((WHATS_NEW_URL, WHATS_NEW_HEADING, WHATS_NEW_TEXT),
                                                           (PEP_URL, PEP_HEADING, PEP_TEXT)))
        self.assertLess(note.index("lower_upper"), note.index("<p>{evil}</p>"))
        self.assertIn(PEP_URL, note)

    def test_an_over_long_section_is_cut_to_its_matching_parts_with_whole_code(self):
        padding = "\n\n".join(f"Unrelated paragraph {number} about the history of packaging." for number in range(200))
        note = docs_reading.read_top_page("", TSTRING_QUESTION, fetch=lambda url: "",
                                          find_local=found((WHATS_NEW_URL, WHATS_NEW_HEADING, padding + "\n\n" + WHATS_NEW_TEXT)))
        self.assertIn("def lower_upper(template):", note)
        self.assertLess(len(note), docs_reading.EXCERPT_CHARS + len(docs_reading.DOCS_NOTE) + 400)
        self.assertEqual(note.count("```") % 2, 0)

    def test_the_web_page_is_read_when_the_library_has_nothing(self):
        fetched = []
        page = "Contents of https://docs.djangoproject.com/en/6.1/topics/tasks/:\n" + DJANGO_PAGE
        note = docs_reading.read_top_page(DJANGO_RESULTS, DJANGO_QUESTION, fetch=lambda url: fetched.append(url) or page,
                                          find_local=found())
        self.assertEqual(fetched, ["https://docs.djangoproject.com/en/6.1/topics/tasks/"])
        self.assertIn("from django.tasks import task", note)

    def test_the_web_is_used_when_the_local_section_never_names_what_was_asked(self):
        fetched = []
        page = "Contents of https://docs.djangoproject.com/en/6.1/topics/tasks/:\n" + DJANGO_PAGE
        unrelated = (DJANGO_LOCAL_URL, "Tasks > Defining Tasks", "Background work is defined with a decorator.\n\n```\n@task\n```")
        docs_reading.read_top_page(DJANGO_RESULTS, DJANGO_QUESTION, fetch=lambda url: fetched.append(url) or page,
                                   find_local=found(unrelated))
        self.assertEqual(fetched, ["https://docs.djangoproject.com/en/6.1/topics/tasks/"])

    def test_the_coding_lookup_carries_the_local_section(self):
        with unittest.mock.patch.object(local_docs, "best_sections", found((WHATS_NEW_URL, WHATS_NEW_HEADING, WHATS_NEW_TEXT))):
            used, notes, _ = lookup_with(DJANGO_RESULTS, {}, question=TSTRING_QUESTION)
        self.assertIn("def lower_upper(template):", notes)
        self.assertIn(docs_reading.FETCH_TOOL, used)


class LibraryClient(unittest.TestCase):
    def setUp(self):
        FakeLibrary.asked = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeLibrary)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_without_a_configured_library_nothing_is_asked(self):
        with unittest.mock.patch.object(local_docs, "LIBRARY_URL", ""):
            self.assertEqual(local_docs.best_sections(TSTRING_QUESTION), [])
        self.assertEqual(FakeLibrary.asked, [])

    def test_good_hits_are_read_in_order_and_weak_ones_skipped(self):
        FakeLibrary.hits = [{"id": 7, "url": WHATS_NEW_URL, "heading": WHATS_NEW_HEADING, "score": 3.1},
                            {"id": 9, "url": PEP_URL, "heading": PEP_HEADING, "score": 2.4},
                            {"id": 11, "url": "https://example.org/weak", "heading": "Weak", "score": 0.9}]
        FakeLibrary.sections = {7: {"text": WHATS_NEW_TEXT}, 9: {"text": PEP_TEXT}}
        with unittest.mock.patch.object(local_docs, "LIBRARY_URL", self.url):
            sections = local_docs.best_sections(TSTRING_QUESTION)
        self.assertEqual([section.url for section in sections], [WHATS_NEW_URL, PEP_URL])
        self.assertEqual(sections[0].text, WHATS_NEW_TEXT)
        self.assertEqual(FakeLibrary.asked[0][1]["q"], [TSTRING_QUESTION])

    def test_an_unreachable_library_gives_nothing_quickly(self):
        with socket.socket() as closed:
            closed.bind(("127.0.0.1", 0))
            unused = f"http://127.0.0.1:{closed.getsockname()[1]}"
        started = time.monotonic()
        with unittest.mock.patch.object(local_docs, "LIBRARY_URL", unused), self.assertLogs("brain.local_docs", "WARNING"):
            self.assertEqual(local_docs.best_sections(TSTRING_QUESTION), [])
        self.assertLess(time.monotonic() - started, local_docs.ASK_SECONDS + 1)

    def test_a_malformed_answer_gives_nothing(self):
        FakeLibrary.hits = [{"url": WHATS_NEW_URL, "score": 3.0}]
        with unittest.mock.patch.object(local_docs, "LIBRARY_URL", self.url), self.assertLogs("brain.local_docs", "WARNING"):
            self.assertEqual(local_docs.best_sections(TSTRING_QUESTION), [])

    def test_the_brain_service_points_the_brain_at_the_library(self):
        unit = (Path(__file__).resolve().parents[2] / "services" / "brain.service").read_text()
        self.assertIn("Environment=DOCS_LIBRARY_URL=http://127.0.0.1:8889", unit)

    def test_the_library_address_comes_from_the_environment(self):
        self.assertEqual(local_docs.LIBRARY_URL, os.environ.get("DOCS_LIBRARY_URL", ""))
        self.assertIn("lookups", SHIPPED_CONFIG)


if __name__ == "__main__":
    unittest.main()

import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

from test_review_tonight import FakeHub, FakeOllama, last_user_note, said  # noqa: E402

import brain  # noqa: E402
import code_check  # noqa: E402
import connections  # noqa: E402
import docs_reading  # noqa: E402
import drafts  # noqa: E402

PAGE_URL = "https://docs.python.org/3/library/string.templatelib.html"
EXCERPT = """## Template strings

To write a t-string, use a `'t'` prefix instead of an `'f'`:

```
>>> pi = 3.14
>>> t't-strings are new in Python {pi!s}!'
Template(strings=('t-strings are new in Python ', '!'), interpolations=(Interpolation(3.14, 'pi', 's', ''),))
```

iter(template)
:   Iterate over the template, yielding each non-empty string and `Interpolation` in the correct order.

value: object
:   The evaluated value of the interpolation."""
NOTES = ("\n\nAlready looked up for this message. Answer the message from this, and only this:\n"
         "[research__web_search]\n1. string.templatelib\n   " + PAGE_URL + "\n   Template strings are a mechanism.\n\n"
         + docs_reading.DOCS_NOTE.format(url=PAGE_URL, kind=docs_reading.OFFICIAL, excerpt=EXCERPT))
LOOKED_UP = (["research__web_search", docs_reading.FETCH_TOOL], NOTES, None)
QUESTION = "How do I use Python 3.14 template strings (t-strings) to build an HTML snippet with the user's name escaped?"

INVENTED = """Here's how:

```python
from string.templatelib import Template
import html

name = "<b>Ann</b>"
snippet = t"<div>{name}</div>"
result = snippet.substitute(name=html.escape(name))
print(result)
```"""
FROM_THE_PAGE = """Here's how:

```python
from html import escape
from string.templatelib import Interpolation

def render(template):
    parts = []
    for part in template:
        if isinstance(part, Interpolation):
            parts.append(escape(str(part.value)))
        else:
            parts.append(part)
    return "".join(parts)

name = "<b>Ann</b>"
print(render(t"<div>{name}</div>"))
```"""


class NamesTheCodeCalls(unittest.TestCase):
    def test_a_method_nothing_shows_is_unconfirmed(self):
        self.assertEqual(code_check.unconfirmed_calls(INVENTED, NOTES), ["substitute"])

    def test_code_built_from_the_page_and_plain_python_passes(self):
        self.assertEqual(code_check.unconfirmed_calls(FROM_THE_PAGE, NOTES), [])

    def test_standard_library_functions_pass_with_or_without_their_import(self):
        code = "```python\nimport tomllib\nwith open('a.toml', 'rb') as f:\n    data = tomllib.load(f)\nprint(html.escape(data))\n```"
        self.assertEqual(code_check.unconfirmed_calls(code, ""), [])

    def test_a_name_that_is_not_in_the_named_module_is_unconfirmed(self):
        code = "```python\nimport json\nprint(json.load_safely('x'))\n```"
        self.assertEqual(code_check.unconfirmed_calls(code, ""), ["load_safely"])

    def test_methods_of_classes_imported_from_the_standard_library_pass(self):
        code = "```python\nfrom pathlib import Path\nfolder = Path('.')\nprint(folder.read_text())\n```"
        self.assertEqual(code_check.unconfirmed_calls(code, ""), [])

    def test_names_the_code_defines_or_imports_pass(self):
        code = ("```python\nfrom fastapi import FastAPI\nclass Shop:\n    def restock(self):\n        pass\n"
                "app = FastAPI()\nShop().restock()\nself.restock()\n```")
        self.assertEqual(code_check.unconfirmed_calls(code, ""), [])

    def test_dots_inside_strings_and_comments_are_not_calls(self):
        code = "```python\nprint('see example.render(x)')\n# then call thing.render(y)\n```"
        self.assertEqual(code_check.unconfirmed_calls(code, ""), [])

    def test_calls_on_a_result_are_checked_too(self):
        code = "```python\nimport json\nprint(json.loads('{}').frobnicate())\n```"
        self.assertEqual(code_check.unconfirmed_calls(code, ""), ["frobnicate"])

    def test_only_python_code_is_checked(self):
        code = "```bash\ncurl example.org | jq .\n```\n```javascript\nconst data = await response.blobify();\n```"
        self.assertEqual(code_check.unconfirmed_calls(code, ""), [])

    def test_repl_examples_are_read_without_their_prompts(self):
        code = "```pycon\n>>> import json\n>>> json.dumps_pretty({})\n```"
        self.assertEqual(code_check.unconfirmed_calls(code, ""), ["dumps_pretty"])

    def test_an_answer_without_code_has_nothing_to_check(self):
        self.assertEqual(code_check.unconfirmed_calls("Use the substitute() method.", NOTES), [])

    def test_the_rewrite_note_names_the_page_and_each_name(self):
        note = code_check.rewrite_note(PAGE_URL, ["substitute", "render_html"])
        self.assertIn(PAGE_URL, note)
        self.assertIn("substitute()", note)
        self.assertIn("render_html()", note)


class PageReadForTheTurn(unittest.TestCase):
    def test_the_page_read_is_found_in_the_notes(self):
        self.assertEqual(docs_reading.page_read_in(NOTES), PAGE_URL)

    def test_notes_without_a_page_or_with_only_an_unconfirmed_note_have_none(self):
        self.assertEqual(docs_reading.page_read_in("[research__web_search]\n1. A result"), "")
        unconfirmed = docs_reading.UNCONFIRMED_NOTE.format(url=PAGE_URL, names="get_json_safely")
        self.assertEqual(docs_reading.page_read_in(unconfirmed), "")


def run_with_lookups(replies, looked_up=LOOKED_UP, draft=None):
    ollama = FakeOllama(replies)
    state = {"messages": [{"role": "user", "content": QUESTION}], "context": {}}

    def streamed(body, on_text, timeout=600, on_thinking=None):
        reply = ollama(f"{brain.OLLAMA_URL}/api/chat", body)
        for line in reply["message"]["content"].splitlines(keepends=True):
            on_text(line)
        return reply

    with unittest.mock.patch.object(connections, "hub", FakeHub(("research__web_search", docs_reading.FETCH_TOOL))), \
            unittest.mock.patch.object(connections, "http_json", side_effect=ollama), \
            unittest.mock.patch.object(connections, "stream_chat", side_effect=streamed), \
            unittest.mock.patch.object(brain.models, "budget_for", return_value=0), \
            unittest.mock.patch.object(brain.models, "find", return_value=None), \
            unittest.mock.patch("agent_loop.system_prompt", return_value=""), \
            unittest.mock.patch("agent_loop.current_context", return_value=""), \
            unittest.mock.patch.object(brain.policy, "select_tools", side_effect=lambda tools, *args, **kwargs: tools):
        outcome = brain.run_agent({"model": "m"}, state, run=brain.AgentRun(looked_up=looked_up, draft=draft))
    return outcome, ollama


def collecting_draft(sent):
    return drafts.Draft(sent.append, lambda text: text, lambda sentence: False)


class AnswersAreCheckedAgainstThePageRead(unittest.TestCase):
    def test_code_calling_an_unconfirmed_method_is_rewritten_once_from_the_page(self):
        outcome, ollama = run_with_lookups([said(INVENTED), said(FROM_THE_PAGE)])
        self.assertEqual(len(ollama.chat_bodies), 2)
        note = last_user_note(ollama.chat_bodies[1])
        self.assertIn("substitute()", note)
        self.assertIn(PAGE_URL, note)
        self.assertEqual(outcome["answer"], FROM_THE_PAGE)

    def test_the_rewrite_is_written_from_the_notes_without_tools(self):
        _, ollama = run_with_lookups([said(INVENTED), said(FROM_THE_PAGE)])
        self.assertFalse(ollama.chat_bodies[1].get("tools"))

    def test_only_one_rewrite_is_asked_for(self):
        outcome, ollama = run_with_lookups([said(INVENTED), said(INVENTED), said(FROM_THE_PAGE)])
        self.assertEqual(len(ollama.chat_bodies), 2)
        self.assertEqual(outcome["answer"], INVENTED)

    def test_code_from_the_page_is_answered_straight_away(self):
        outcome, ollama = run_with_lookups([said(FROM_THE_PAGE)])
        self.assertEqual(len(ollama.chat_bodies), 1)
        self.assertEqual(outcome["answer"], FROM_THE_PAGE)

    def test_without_a_page_read_the_code_is_not_checked(self):
        looked_up = (["research__web_search"], "\n\n[research__web_search]\n1. A result", None)
        outcome, ollama = run_with_lookups([said(INVENTED)], looked_up=looked_up)
        self.assertEqual(len(ollama.chat_bodies), 1)
        self.assertEqual(outcome["answer"], INVENTED)

    def test_a_reply_that_may_be_rewritten_is_not_streamed_and_the_rewrite_is(self):
        sent = []
        draft = collecting_draft(sent)
        outcome, _ = run_with_lookups([said(INVENTED), said(FROM_THE_PAGE)], draft=draft)
        self.assertNotIn("substitute", "".join(sent))
        self.assertEqual(draft.finish(outcome["answer"]), FROM_THE_PAGE)

    def test_a_reply_that_passes_the_check_is_still_given_whole(self):
        sent = []
        draft = collecting_draft(sent)
        outcome, _ = run_with_lookups([said(FROM_THE_PAGE)], draft=draft)
        self.assertEqual(draft.finish(outcome["answer"]), FROM_THE_PAGE)


if __name__ == "__main__":
    unittest.main()

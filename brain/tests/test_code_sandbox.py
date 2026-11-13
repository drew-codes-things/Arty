import io
import json
import os
import sys
import unittest
import unittest.mock
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

from test_review_tonight import FakeHub, FakeOllama, last_user_note, said  # noqa: E402

import brain  # noqa: E402
import code_sandbox  # noqa: E402
import connections  # noqa: E402
import drafts  # noqa: E402
import settings  # noqa: E402

RUNNER = "http://runner.test:8790"
QUESTION = "write a python function that checks whether a string is a palindrome, ignoring case and spaces"
BROKEN = """Here's a function:

```python
def is_palindrome(text):
    cleaned = text.replace(" ", "").lower()
    return cleaned == cleaned.reversed()

print(is_palindrome("Never odd or even"))
```"""
FIXED = """Here's a function:

```python
def is_palindrome(text):
    cleaned = text.replace(" ", "").lower()
    return cleaned == cleaned[::-1]

print(is_palindrome("Never odd or even"))
```"""
PASSED = {"status": "passed", "checked": ["syntax", "lint", "run"], "notes": [], "tool": "Python 3.13", "seconds": 0.3}
FAILED = {"status": "failed", "step": "run", "checked": ["syntax", "lint"], "notes": [], "tool": "Python 3.13", "seconds": 0.3,
          "problem": "Traceback (most recent call last):\n  File \"main.py\", line 3, in is_palindrome\n"
                     "AttributeError: 'str' object has no attribute 'reversed'"}
UNCHECKED = {"status": "unchecked", "checked": [], "notes": ["it needs the serde crate, which the sandbox doesn't have"],
             "tool": "Rust", "seconds": 0.0}


def fence(tag: str, code: str) -> str:
    return f"```{tag}\n{code}\n```"


class FindsTheCode(unittest.TestCase):
    def test_tagged_blocks_are_found_with_their_language(self):
        answer = "Intro\n\n" + fence("py", "import os\nprint(os.getcwd())") + "\n\nAnd\n\n" + fence("rs", "fn main() {\n    let x = 1;\n}")
        units = code_sandbox.units_in(answer)
        self.assertEqual(["python", "rust"], [unit.language for unit in units])
        self.assertEqual("import os\nprint(os.getcwd())\n", units[0].code)

    def test_indented_blocks_inside_lists_are_dedented(self):
        answer = "1. Run this:\n\n   ```python\n   total = 0\n   for n in range(3):\n       total += n\n   ```\n"
        self.assertEqual("total = 0\nfor n in range(3):\n    total += n\n", code_sandbox.units_in(answer)[0].code)

    def test_every_supported_alias_maps_to_a_runner_language(self):
        for alias, language in {"js": "javascript", "ts": "typescript", "sh": "bash", "c++": "cpp", "golang": "go",
                                "kt": "kotlin", "c#": "csharp", "rb": "ruby", "ps1": "powershell", "yml": "yaml",
                                "postgresql": "sql"}.items():
            with self.subTest(alias=alias):
                self.assertEqual(language, code_sandbox.language_of(alias, ""))

    def test_output_and_unknown_blocks_are_skipped(self):
        answer = "\n".join([fence("text", "hello\nworld"), fence("console", "$ ls\nfile"), fence("haskell", "main = do\n  print 1")])
        self.assertEqual([], code_sandbox.units_in(answer))

    def test_untagged_blocks_are_only_checked_when_the_language_is_certain(self):
        self.assertEqual("python", code_sandbox.language_of("", "#!/usr/bin/env python3\nprint(1)\n"))
        self.assertEqual("c", code_sandbox.language_of("", "#include <stdio.h>\nint main(void) { return 0; }\n"))
        self.assertEqual("go", code_sandbox.language_of("", "package main\n\nfunc main() {}\n"))
        self.assertEqual("", code_sandbox.language_of("", "x = 1\ny = 2\n"))

    def test_trivial_one_liners_and_install_commands_are_skipped(self):
        answer = "\n\n".join([fence("python", "print('hi')"), fence("bash", "pip install requests\nnpm install express"),
                                fence("bash", "for f in *.txt; do\n  echo \"$f\"\ndone")])
        units = code_sandbox.units_in(answer)
        self.assertEqual(["bash"], [unit.language for unit in units])

    def test_repl_examples_are_read_without_prompts_or_output(self):
        answer = fence("pycon", ">>> values = [3, 1, 2]\n>>> sorted(values)\n[1, 2, 3]")
        self.assertEqual("values = [3, 1, 2]\nsorted(values)\n", code_sandbox.units_in(answer)[0].code)

    def test_script_blocks_of_one_language_are_checked_together(self):
        answer = fence("python", "def add(a, b):\n    return a + b") + "\nUse it:\n" + fence("python", "print(add(1, 2))\nprint(add(3, 4))")
        units = code_sandbox.units_in(answer)
        self.assertEqual(1, len(units))
        self.assertIn("def add", units[0].code)
        self.assertIn("print(add(1, 2))", units[0].code)

    def test_javascript_blocks_that_redeclare_a_name_are_checked_apart(self):
        answer = fence("js", "const total = 1;\nconsole.log(total);") + "\n" + fence("js", "const total = 2;\nconsole.log(total);")
        self.assertEqual(["JavaScript (block 1)", "JavaScript (block 2)"], [unit.label for unit in code_sandbox.units_in(answer)])

    def test_a_c_function_and_its_main_are_joined_with_main_last(self):
        answer = fence("c", "#include <stdio.h>\nint main(void) {\n    printf(\"%d\\n\", twice(2));\n}") + "\n" + \
            fence("c", "int twice(int n) {\n    return n * 2;\n}")
        units = code_sandbox.units_in(answer)
        self.assertEqual(1, len(units))
        self.assertLess(units[0].code.index("int twice"), units[0].code.index("int main"))

    def test_python_314_is_asked_for_by_the_question_or_the_code(self):
        self.assertEqual("3.14", code_sandbox.python_version("How do I use Python 3.14 t-strings?", "x = 1"))
        self.assertEqual("3.14", code_sandbox.python_version("", "from string.templatelib import Template"))
        self.assertEqual("3.14", code_sandbox.python_version("", "page = t\"<p>{name}</p>\""))
        self.assertEqual("", code_sandbox.python_version("python 3.11 please", "print(f'{x}')\nprint(\"don't\")"))

    def test_the_sql_dialect_comes_from_the_tag_or_the_question(self):
        self.assertEqual("PostgreSQL", code_sandbox.sql_dialect("postgresql", ""))
        self.assertEqual("MySQL", code_sandbox.sql_dialect("sql", "a MySQL query for the top 5 users"))
        self.assertEqual("", code_sandbox.sql_dialect("sql", "a query for the top 5 users"))


class TalksToTheRunner(unittest.TestCase):
    unit = code_sandbox.Unit("python", "print(1)\nprint(2)\n", "", "Python")

    def checked(self, side_effect):
        with unittest.mock.patch.object(settings, "CODE_RUNNER_URL", RUNNER), \
                unittest.mock.patch.object(code_sandbox, "post_check", side_effect=side_effect):
            return code_sandbox.checked_unit(self.unit, 5)

    def test_a_verdict_is_returned(self):
        self.assertEqual(PASSED, self.checked(lambda *args: PASSED))

    def test_an_unreachable_runner_is_reported(self):
        with self.assertRaisesRegex(code_sandbox.RunnerUnavailable, "couldn't reach"):
            self.checked(urllib.error.URLError("refused"))

    def test_a_timeout_is_reported(self):
        with self.assertRaisesRegex(code_sandbox.RunnerUnavailable, "couldn't reach"):
            self.checked(TimeoutError("timed out"))

    def test_an_http_error_is_reported_with_its_detail(self):
        error = urllib.error.HTTPError(RUNNER, 503, "busy", {}, io.BytesIO(b'{"error": "the runner is busy"}'))
        with self.assertRaisesRegex(code_sandbox.RunnerUnavailable, "503.*busy"):
            self.checked(error)

    def test_a_broken_or_unknown_answer_is_reported(self):
        with self.assertRaisesRegex(code_sandbox.RunnerUnavailable, "JSON"):
            self.checked(json.JSONDecodeError("bad", "x", 0))
        with self.assertRaisesRegex(code_sandbox.RunnerUnavailable, "without a verdict"):
            self.checked(lambda *args: {"status": "great"})

    def test_units_the_runner_could_not_check_have_no_verdict(self):
        units = [self.unit, code_sandbox.Unit("rust", "fn main() {}\n", "", "Rust")]

        def answer(url, unit, timeout):
            if unit.language == "rust":
                raise urllib.error.URLError("refused")
            return PASSED
        with unittest.mock.patch.object(settings, "CODE_RUNNER_URL", RUNNER), \
                unittest.mock.patch.object(code_sandbox, "post_check", side_effect=answer):
            self.assertEqual([PASSED, None], code_sandbox.checked_units(units))

    def test_the_request_names_the_language_code_and_version(self):
        sent = {}

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def opened(request, timeout):
            sent.update(url=request.full_url, body=json.loads(request.data), timeout=timeout)
            return Response(json.dumps(PASSED).encode())
        with unittest.mock.patch("urllib.request.urlopen", side_effect=opened):
            code_sandbox.post_check(RUNNER, code_sandbox.Unit("python", "print(1)\n", "3.14", "Python"), 7)
        self.assertEqual(f"{RUNNER}/check", sent["url"])
        self.assertEqual({"language": "python", "code": "print(1)\n", "version": "3.14"}, sent["body"])
        self.assertEqual(7, sent["timeout"])


class SaysWhatWasChecked(unittest.TestCase):
    def report(self, *verdicts):
        units = [code_sandbox.Unit("python", "x", "", "Python"), code_sandbox.Unit("rust", "x", "", "Rust")][:len(verdicts)]
        return code_sandbox.Report(units, list(verdicts), 1.0)

    def test_code_that_ran_says_so(self):
        self.assertEqual("Sandbox check: the Python ran without errors (Python 3.13).",
                         code_sandbox.summary(self.report(PASSED)))

    def test_code_that_could_not_be_run_says_why(self):
        verdict = {**PASSED, "checked": ["syntax", "lint"], "notes": ["not run: it needs the requests package"]}
        self.assertEqual("Sandbox check: the Python has no syntax errors (Python 3.13) (not run: it needs the requests package).",
                         code_sandbox.summary(self.report(verdict)))

    def test_code_that_still_fails_shows_the_error(self):
        text = code_sandbox.summary(self.report(FAILED))
        self.assertIn("still fails", text)
        self.assertIn("AttributeError: 'str' object has no attribute 'reversed'", text)

    def test_unchecked_code_never_claims_a_check(self):
        text = code_sandbox.summary(self.report(PASSED, UNCHECKED))
        self.assertIn("the Rust couldn't be checked (it needs the serde crate", text)
        self.assertNotIn("Rust ran", text)

    def test_the_rewrite_note_carries_the_error(self):
        note = code_sandbox.rewrite_note([(code_sandbox.Unit("python", "x", "", "Python"), FAILED)])
        self.assertIn("AttributeError", note)
        self.assertIn("run step (Python 3.13)", note)
        self.assertIn("whole answer again", note)


def run_coding_turn(replies, verdicts, question=QUESTION, draft=None, runner=RUNNER):
    ollama = FakeOllama(replies)
    verdicts = iter(verdicts)
    checks = []
    state = {"messages": [{"role": "user", "content": question}], "context": {}}

    def streamed(body, on_text, timeout=600, on_thinking=None):
        reply = ollama(f"{brain.OLLAMA_URL}/api/chat", body)
        for line in reply["message"]["content"].splitlines(keepends=True):
            on_text(line)
        return reply

    def checked(url, unit, timeout):
        checks.append(unit)
        verdict = next(verdicts)
        if isinstance(verdict, Exception):
            raise verdict
        return verdict

    with unittest.mock.patch.object(connections, "hub", FakeHub()), \
            unittest.mock.patch.object(connections, "http_json", side_effect=ollama), \
            unittest.mock.patch.object(connections, "stream_chat", side_effect=streamed), \
            unittest.mock.patch.object(brain.models, "budget_for", return_value=0), \
            unittest.mock.patch.object(brain.models, "find", return_value=None), \
            unittest.mock.patch("agent_loop.system_prompt", return_value=""), \
            unittest.mock.patch("agent_loop.current_context", return_value=""), \
            unittest.mock.patch.object(brain.policy, "select_tools", side_effect=lambda tools, *args, **kwargs: tools), \
            unittest.mock.patch.object(settings, "CODE_RUNNER_URL", runner), \
            unittest.mock.patch.object(code_sandbox, "post_check", side_effect=checked):
        outcome = brain.run_agent({"model": "m"}, state, run=brain.AgentRun(draft=draft))
    return outcome, ollama, checks


class RewritesFromRealErrors(unittest.TestCase):
    def test_failing_code_is_sent_back_with_the_error_and_the_fix_is_given(self):
        outcome, ollama, checks = run_coding_turn([said(BROKEN), said(FIXED)], [FAILED, PASSED])
        self.assertEqual(2, len(ollama.chat_bodies))
        self.assertIn("AttributeError: 'str' object has no attribute 'reversed'", last_user_note(ollama.chat_bodies[1]))
        self.assertFalse(ollama.chat_bodies[1].get("tools"))
        self.assertTrue(outcome["answer"].startswith(FIXED))
        self.assertTrue(outcome["answer"].endswith("Sandbox check: the Python ran without errors (Python 3.13)."))
        self.assertIn("code_sandbox", outcome["tools_used"])
        self.assertEqual(2, len(checks))

    def test_at_most_two_rewrites_then_the_error_is_admitted(self):
        outcome, ollama, checks = run_coding_turn([said(BROKEN), said(BROKEN), said(BROKEN), said(FIXED)],
                                                  [FAILED, FAILED, FAILED, PASSED])
        self.assertEqual(3, len(ollama.chat_bodies))
        self.assertEqual(3, len(checks))
        self.assertTrue(outcome["answer"].startswith(BROKEN))
        self.assertIn("still fails", outcome["answer"])

    def test_code_that_passes_is_answered_straight_away(self):
        outcome, ollama, _ = run_coding_turn([said(FIXED)], [PASSED])
        self.assertEqual(1, len(ollama.chat_bodies))
        self.assertIn("ran without errors", outcome["answer"])

    def test_code_that_cannot_be_checked_is_not_rewritten_and_says_so(self):
        answer = "Use serde:\n\n```rust\nuse serde::Serialize;\n\n#[derive(Serialize)]\nstruct Point { x: i32 }\n```"
        outcome, ollama, _ = run_coding_turn([said(answer)], [UNCHECKED], question="write a rust program that uses serde to serialize a struct")
        self.assertEqual(1, len(ollama.chat_bodies))
        self.assertIn("couldn't be checked", outcome["answer"])
        self.assertNotIn("code_sandbox", outcome["tools_used"])

    def test_when_the_runner_is_down_the_answer_is_given_unchanged(self):
        outcome, ollama, _ = run_coding_turn([said(BROKEN)], [urllib.error.URLError("refused")])
        self.assertEqual(1, len(ollama.chat_bodies))
        self.assertEqual(BROKEN, outcome["answer"])

    def test_without_a_runner_nothing_is_checked(self):
        outcome, _, checks = run_coding_turn([said(BROKEN)], [], runner="")
        self.assertEqual([], checks)
        self.assertEqual(BROKEN, outcome["answer"])

    def test_questions_that_are_not_about_code_are_not_checked(self):
        outcome, _, checks = run_coding_turn([said("Tea:\n\n```python\nprint('steep')\nprint('pour')\n```")], [],
                                             question="how long should I steep green tea?")
        self.assertEqual([], checks)

    def test_a_coding_reply_is_held_back_while_it_may_be_rewritten(self):
        sent = []
        draft = drafts.Draft(sent.append, lambda text: text, lambda sentence: False)
        outcome, _, _ = run_coding_turn([said(BROKEN), said(FIXED)], [FAILED, PASSED], draft=draft)
        self.assertEqual([], sent)
        self.assertTrue(draft.finish(outcome["answer"]).startswith(FIXED))


if __name__ == "__main__":
    unittest.main()

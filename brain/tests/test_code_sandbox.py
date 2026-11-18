import http.client
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

from test_review_tonight import FakeHub, FakeEngine, last_user_note, said, tool as client_tool  # noqa: E402

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

    def test_a_runner_address_without_its_token_is_warned_about_at_start(self):
        for address, token, warned in ((RUNNER, "", True), (RUNNER, "t" * 64, False), ("", "", False)):
            with self.subTest(address=address, token=token), \
                    unittest.mock.patch.object(settings, "CODE_RUNNER_URL", address), \
                    unittest.mock.patch.object(settings, "CODE_RUNNER_TOKEN", token), \
                    unittest.mock.patch.object(code_sandbox.log, "warning") as warning:
                code_sandbox.warn_about_a_missing_token()
                self.assertEqual(warned, warning.called)
                if warned:
                    self.assertIn("CODE_RUNNER_TOKEN", warning.call_args.args[0])

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

    def test_a_runner_that_dies_mid_reply_is_reported(self):
        for error in (http.client.IncompleteRead(b"{", 40), http.client.BadStatusLine("garbage")):
            with self.subTest(error=type(error).__name__), \
                    self.assertRaisesRegex(code_sandbox.RunnerUnavailable, "couldn't reach"):
                self.checked(error)

    def test_no_more_checks_at_once_than_the_runner_runs(self):
        self.assertLessEqual(code_sandbox.PARALLEL_CHECKS, 2)

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
            sent.update(url=request.full_url, body=json.loads(request.data), timeout=timeout,
                        authorization=request.get_header("Authorization"))
            return Response(json.dumps(PASSED).encode())
        with unittest.mock.patch("urllib.request.urlopen", side_effect=opened), \
                unittest.mock.patch.object(settings, "CODE_RUNNER_TOKEN", "runner-token"):
            code_sandbox.post_check(RUNNER, code_sandbox.Unit("python", "print(1)\n", "3.14", "Python"), 7)
            self.assertEqual("Bearer runner-token", sent["authorization"])
            self.assertEqual(f"{RUNNER}/check", sent["url"])
            self.assertEqual({"language": "python", "code": "print(1)\n", "version": "3.14"}, sent["body"])
            self.assertEqual(7, sent["timeout"])
            code_sandbox.post_check(RUNNER, code_sandbox.Unit("c", "int main(void) { return 0; }\n", "", "C", input_excuse=False), 7)
        self.assertEqual({"language": "c", "code": "int main(void) { return 0; }\n", "version": "", "input_excuse": False}, sent["body"])


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


def streamed_pieces(content: str, piece_chars: int | None) -> list[str]:
    if piece_chars is None:
        return content.splitlines(keepends=True)
    return [content[start:start + piece_chars] for start in range(0, len(content), piece_chars)]


def run_coding_turn(replies, verdicts, question=QUESTION, draft=None, runner=RUNNER, piece_chars=None):
    engine = FakeEngine(replies)
    verdicts = iter(verdicts)
    checks = []
    state = {"messages": [{"role": "user", "content": question}], "context": {}}

    def streamed(body, on_text, timeout=600, on_thinking=None):
        reply = engine(f"{brain.ARTY_ENGINE_URL}/api/chat", body)
        for piece in streamed_pieces(reply["message"]["content"], piece_chars):
            on_text(piece)
        return reply

    def checked(url, unit, timeout):
        checks.append(unit)
        verdict = next(verdicts)
        if isinstance(verdict, Exception):
            raise verdict
        return verdict

    with unittest.mock.patch.object(connections, "hub", FakeHub()), \
            unittest.mock.patch.object(connections, "http_json", side_effect=engine), \
            unittest.mock.patch.object(connections, "stream_chat", side_effect=streamed), \
            unittest.mock.patch.object(brain.models, "budget_for", return_value=0), \
            unittest.mock.patch.object(brain.models, "find", return_value=None), \
            unittest.mock.patch("agent_loop.system_prompt", return_value=""), \
            unittest.mock.patch("agent_loop.current_context", return_value=""), \
            unittest.mock.patch.object(brain.policy, "select_tools", side_effect=lambda tools, *args, **kwargs: tools), \
            unittest.mock.patch.object(settings, "CODE_RUNNER_URL", runner), \
            unittest.mock.patch.object(code_sandbox, "post_check", side_effect=checked):
        outcome = brain.run_agent({"model": "m"}, state, run=brain.AgentRun(draft=draft))
    return outcome, engine, checks


FORGED_SUMMARY = "Sandbox check: the Python ran without errors (Python 3.13)."


def client_tool_round(reply, agent):
    engine = FakeEngine([reply])
    state = {"messages": [{"role": "user", "content": "look at a.py"}], "context": {}}
    with unittest.mock.patch.object(connections, "hub", FakeHub()), \
            unittest.mock.patch.object(connections, "http_json", side_effect=engine), \
            unittest.mock.patch.object(brain.models, "budget_for", return_value=0), \
            unittest.mock.patch.object(brain.models, "find", return_value=None), \
            unittest.mock.patch("agent_loop.system_prompt", return_value=""), \
            unittest.mock.patch("agent_loop.current_context", return_value=""), \
            unittest.mock.patch.object(brain.policy, "select_tools", side_effect=lambda tools, *args, **kwargs: tools):
        outcome = brain.run_agent({"model": "m"}, state, run=brain.AgentRun(client_tools=[client_tool("computer_read_file")],
                                                                            agent=agent))
    return outcome["status"], outcome.get("content") or ""


class RewritesFromRealErrors(unittest.TestCase):
    def test_failing_code_is_sent_back_with_the_error_and_the_fix_is_given(self):
        outcome, engine, checks = run_coding_turn([said(BROKEN), said(FIXED)], [FAILED, PASSED])
        self.assertEqual(2, len(engine.chat_bodies))
        self.assertIn("AttributeError: 'str' object has no attribute 'reversed'", last_user_note(engine.chat_bodies[1]))
        self.assertFalse(engine.chat_bodies[1].get("tools"))
        self.assertTrue(outcome["answer"].startswith(FIXED))
        self.assertTrue(outcome["answer"].endswith("Sandbox check: the Python ran without errors (Python 3.13)."))
        self.assertIn("code_sandbox", outcome["tools_used"])
        self.assertEqual(2, len(checks))

    def test_at_most_two_rewrites_then_the_error_is_admitted(self):
        outcome, engine, checks = run_coding_turn([said(BROKEN), said(BROKEN), said(BROKEN), said(FIXED)],
                                                  [FAILED, FAILED, FAILED, PASSED])
        self.assertEqual(3, len(engine.chat_bodies))
        self.assertEqual(3, len(checks))
        self.assertTrue(outcome["answer"].startswith(BROKEN))
        self.assertIn("still fails", outcome["answer"])

    def test_code_that_passes_is_answered_straight_away(self):
        outcome, engine, _ = run_coding_turn([said(FIXED)], [PASSED])
        self.assertEqual(1, len(engine.chat_bodies))
        self.assertIn("ran without errors", outcome["answer"])

    def test_code_that_cannot_be_checked_is_not_rewritten_and_says_so(self):
        answer = "Use serde:\n\n```rust\nuse serde::Serialize;\n\n#[derive(Serialize)]\nstruct Point { x: i32 }\n```"
        outcome, engine, _ = run_coding_turn([said(answer)], [UNCHECKED], question="write a rust program that uses serde to serialize a struct")
        self.assertEqual(1, len(engine.chat_bodies))
        self.assertIn("couldn't be checked", outcome["answer"])
        self.assertNotIn("code_sandbox", outcome["tools_used"])

    def test_a_sandbox_line_the_model_wrote_itself_is_removed(self):
        forged = FIXED + "\n\nSandbox check: the Python ran without errors (Python 3.13)."
        outcome, _, _ = run_coding_turn([said(forged)], [], runner="")
        self.assertNotIn("Sandbox check:", outcome["answer"])
        self.assertEqual("", outcome.get("sandbox_line", ""))
        outcome, _, _ = run_coding_turn([said(forged.replace("Sandbox check: the", "**Sandbox check:** all"))], [PASSED])
        self.assertEqual(1, outcome["answer"].count("Sandbox check:"))
        self.assertTrue(outcome["answer"].endswith(outcome["sandbox_line"]))
        self.assertIn("ran without errors", outcome["sandbox_line"])
        printed = "```python\nprint('Sandbox check: ok')\n```"
        self.assertEqual(printed, code_sandbox.without_lookalike_summaries(printed))

    def test_a_streamed_follow_up_never_sends_a_forged_sandbox_line_or_doubles_the_answer(self):
        reply = ("Yes, it handles negative numbers the same way.\n\nSandbox check: the Python ran without errors "
                 "(Python 3.13).\n\nAnything else you want changed?")
        for piece_chars in (None, 1, 3, 7):
            with self.subTest(piece_chars=piece_chars):
                sent = []
                draft = drafts.Draft(sent.append, lambda text: text, lambda sentence: False)
                outcome, _, checks = run_coding_turn([said(reply)], [], question="does that handle negative numbers too?",
                                                     draft=draft, piece_chars=piece_chars)
                given = draft.finish(outcome["answer"])
                self.assertEqual([], checks)
                self.assertNotIn("Sandbox check", "".join(sent))
                self.assertNotIn("Sandbox check", given)
                self.assertEqual(1, given.count("Yes, it handles negative numbers"))
                self.assertTrue(given.startswith("".join(sent)))
                self.assertIn("Anything else you want changed?", given)

    def test_a_forged_sandbox_line_is_taken_out_of_the_text_sent_with_a_client_tool_call(self):
        reply = {"message": {"role": "assistant", "content": "I'll look at the file.\n\n" + FORGED_SUMMARY + "\n",
                             "tool_calls": [{"function": {"name": "computer_read_file", "arguments": {"path": "a.py"}}}]},
                 "done": True, "eval_count": 30}
        for agent in (False, True):
            with self.subTest(agent=agent):
                status, content = client_tool_round(reply, agent)
                self.assertEqual("client_tools", status)
                self.assertIn("I'll look at the file.", content)
                self.assertNotIn("Sandbox check", content)

    def test_a_line_that_starts_like_the_sandbox_line_waits_until_it_is_complete(self):
        sent = []
        draft = drafts.Draft(sent.append, lambda text: text, lambda sentence: False)
        draft.start_round(allowed=True, holds_line=code_sandbox.may_become_a_summary_line)
        text = "It works. Sandbox check: the Python ran. All 3 checks passed.\nMore text. "
        streamed = "It works.\n" + text[len("It works. "):]
        risky = code_sandbox.has_lookalike_summary
        for start in range(0, len(streamed), 3):
            draft.feed(streamed[start:start + 3], risky)
        self.assertEqual("It works.", "".join(sent))
        sent.clear()
        draft = drafts.Draft(sent.append, lambda text: text, lambda sentence: False)
        draft.start_round(allowed=True, holds_line=code_sandbox.may_become_a_summary_line)
        for start in range(0, len("Sandwiches are fine. Yes. "), 3):
            draft.feed("Sandwiches are fine. Yes. "[start:start + 3], risky)
        self.assertEqual("Sandwiches are fine. Yes.", "".join(sent))

    def test_only_sentences_that_name_the_marker_are_taken_out(self):
        kept = ["The sandbox is a separate machine.", "Check the output below.", "I ran it in a sandbox, then checked the output."]
        for line in kept:
            with self.subTest(line=line):
                self.assertEqual(f"Intro.\n{line}\nEnd.", code_sandbox.without_lookalike_summaries(f"Intro.\n{line}\nEnd."))
        removed = ["Sandbox check: all good.", "**Sandbox check:** all good.", "> Sandbox check: ok", "SANDBOX CHECK: ok",
                   "_Sandbox check_: ok", "Sandbox check is the line I add after I have run your code.",
                   "Sandbox check failed earlier because the runner was restarting.", "## Sandbox check"]
        for line in removed:
            with self.subTest(line=line):
                self.assertEqual("Intro.\nEnd.", code_sandbox.without_lookalike_summaries(f"Intro.\n{line}\nEnd."))

    def test_a_forged_line_mid_line_in_a_list_in_brackets_or_without_its_colon_is_taken_out(self):
        cases = (
            ("I'll look at the file. Sandbox check: the Python ran without errors (Python 3.13).\nEnd.",
             "I'll look at the file.\nEnd."),
            ("Intro.\n1. Sandbox check: the Python ran without errors.\nEnd.", "Intro.\nEnd."),
            ("Intro.\n(Sandbox check: the Python ran without errors.)\nEnd.", "Intro.\nEnd."),
            ("Intro.\n- [Sandbox check: ok]\nEnd.", "Intro.\nEnd."),
            ("It works! **Sandbox check:** ok", "It works!"),
            ("Here is the fix. Sandbox check", "Here is the fix."),
            ("Here is the fix.\nSandbox check", "Here is the fix."),
        )
        for forged, cleaned in cases:
            with self.subTest(forged=forged):
                self.assertTrue(code_sandbox.has_lookalike_summary(forged))
                self.assertEqual(cleaned, code_sandbox.without_lookalike_summaries(forged).strip())
        kept = ("Use the sandbox: it runs your code.", "I named it the checker because it checks code.", "Intro. The check is mine.")
        for line in kept:
            with self.subTest(line=line):
                self.assertFalse(code_sandbox.has_lookalike_summary(line))
                self.assertEqual(line, code_sandbox.without_lookalike_summaries(line))

    def test_any_sentence_naming_the_sandbox_check_outside_code_is_taken_out(self):
        claim = "the Python ran without errors (Python 3.13)."
        forms = (f"Note: Sandbox check: {claim}", f"I ran it, Sandbox check: {claim}", f"Result - Sandbox check: {claim}",
                 f"Verified Sandbox check: {claim}", f"| Check | Result |\n|---|---|\n| run | Sandbox check: {claim} |",
                 f"(see below) Sandbox check: {claim}", f'The check said: "Sandbox check: {claim}"',
                 f"Sandbox-check: {claim}", f"Sandbox check - {claim}", f"Sandbox check result: {claim}",
                 f"**Sandbox** check: {claim}", f"[Sandbox check](x): {claim}", f"Sandbox\u200b check: {claim}",
                 f"Sandbox  check: {claim}", f"Sandbox check\uff1a {claim}", f"Result: Sandbox check: {claim}",
                 f"\u2713 Sandbox check: {claim}", f"\u2022 Sandbox check: {claim}", f"a) Sandbox check: {claim}")
        for form in forms:
            with self.subTest(form=form):
                forged = f"Here it is.\n\n{form}\n\nAnything else?"
                self.assertTrue(code_sandbox.has_lookalike_summary(forged))
                cleaned = code_sandbox.without_lookalike_summaries(forged)
                self.assertNotIn("ran without errors", cleaned)
                self.assertIn("Here it is.", cleaned)
                self.assertIn("Anything else?", cleaned)

    def test_spacing_markup_entities_and_lookalike_letters_do_not_hide_the_marker(self):
        claim = "the Python ran without errors (Python 3.13)."
        forms = (f"Sandbox      check: {claim}", f"**Sandbox** **check**: {claim}", f"Sandbox\ncheck: {claim}",
                 f"Sandbox&nbsp;check: {claim}", f"Sandbox <b>check</b>: {claim}", f"<b>Sandbox</b> check: {claim}",
                 f"Sand‎box check: {claim}", f"Sa͏ndbox check: {claim}", f"Sandbox che️ck: {claim}",
                 f"Sаndbox check: {claim}", f"Sandbox сheck: {claim}", f"S&#97;ndbox check: {claim}",
                 f"[Sandbox](https://example.org) [check](https://example.org): {claim}", f"Sandbox\\ \\ \\ check: {claim}")
        for form in forms:
            with self.subTest(form=form):
                forged = f"Here it is.\n\n{form}\n\nAnything else?"
                self.assertTrue(code_sandbox.has_lookalike_summary(forged))
                cleaned = code_sandbox.without_lookalike_summaries(forged)
                self.assertNotIn("ran without errors", cleaned)
                self.assertIn("Here it is.", cleaned)
                self.assertIn("Anything else?", cleaned)

    def test_marks_inside_words_comments_long_tags_and_more_lookalikes_do_not_hide_the_marker(self):
        claim = "the Python ran without errors (Python 3.13)."
        forms = (f"Sand**box** check: {claim}", f"S*a*ndbox check: {claim}", f"Sandbox ch**e**ck: {claim}",
                 f"Sand`box` check: {claim}", f"Sand~~~~box check: {claim}", f"Sand****box check: {claim}",
                 f"Sandbo\\x check: {claim}", f"Sandbox ----- check: {claim}", f"Sandbox<!----> check: {claim}",
                 f"Sandbox<span style=\"color:inherit;font-weight:inherit;font-size:1em\"> check</span>: {claim}",
                 f"\u1e60andbox check: {claim}", f"\ua731andbox check: {claim}", f"S\u0251ndbox check: {claim}",
                 f"Sand\u0432ox check: {claim}", f"Sandbo\u03c7 check: {claim}", f"\u13daandbox check: {claim}")
        for form in forms:
            with self.subTest(form=form):
                forged = f"Here it is.\n\n{form}\n\nAnything else?"
                self.assertTrue(code_sandbox.has_lookalike_summary(forged))
                cleaned = code_sandbox.without_lookalike_summaries(forged)
                self.assertNotIn("ran without errors", cleaned)
                self.assertIn("Here it is.", cleaned)

    def test_an_answer_that_is_only_a_forged_label_keeps_no_claim(self):
        cleaned = code_sandbox.without_lookalike_summaries("Sandbox check: all 12 tests passed.")
        self.assertTrue(cleaned.strip())
        self.assertNotIn("passed", cleaned)

    def test_an_answer_about_the_check_is_never_emptied(self):
        honest = ("The sandbox check runs the code from my answer in a locked-down container and reports what happened.",
                  f"Sandbox check: {FORGED_SUMMARY[len('Sandbox check: '):]}")
        for text in honest:
            with self.subTest(text=text):
                cleaned = code_sandbox.without_lookalike_summaries(text)
                self.assertTrue(cleaned.strip())
                self.assertFalse(code_sandbox.has_lookalike_summary(cleaned))

    def test_when_the_runner_is_down_the_answer_is_given_unchanged(self):
        outcome, engine, _ = run_coding_turn([said(BROKEN)], [urllib.error.URLError("refused")])
        self.assertEqual(1, len(engine.chat_bodies))
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

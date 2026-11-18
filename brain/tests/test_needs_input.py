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

from test_code_sandbox import FAILED, PASSED, RUNNER, run_coding_turn  # noqa: E402
from test_review_tonight import last_user_note, said  # noqa: E402

import code_sandbox  # noqa: E402
import settings  # noqa: E402
import stated_input  # noqa: E402

READS_TWO_NUMBERS = """Here's the program:

```python
first, second = map(int, input().split())
print(first + second)
```"""
NEEDS_INPUT = {"status": "needs_input", "step": "run", "checked": ["syntax", "lint"], "tool": "Python 3.13", "seconds": 0.3,
               "notes": ["it reads input or command-line arguments, and the sandbox gave it none"],
               "problem": "Traceback (most recent call last):\nValueError: not enough values to unpack (expected 2, got 0)"}
FAILED_ON_MADE_UP_INPUT = {
    "status": "failed", "step": "run", "checked": ["syntax", "lint"], "notes": [], "tool": "Python 3.13", "seconds": 0.3,
    "problem": "Run again with made-up input (8 lines of '1' on stdin), it failed like this:\nTraceback (most recent call last):\n"
               "  File \"main.py\", line 3, in <module>\nValueError: invalid literal for int() with base 10: '1\\n1'"}
FAILED_BEFORE_RUNNING = {**FAILED_ON_MADE_UP_INPUT, "step": "lint", "problem": "main.py:2:7: undefined name 'totl'"}
WITH_INPUT = "write a python program that reads two numbers and prints their sum\n\nInput:\n```\n3 4\n```\nOutput: 7"
WITHOUT_INPUT = "write a python program that reads two numbers and prints their sum"

STATED_INPUTS = (
    ("labelled fenced block", "sum them\n\nInput:\n```\n3 4\n```\nOutput: 7", stated_input.StatedInput(stdin="3 4\n")),
    ("labelled plain lines", "sum them\n\nSample input:\n3\n1 2 3\n\nSample output:\n6",
     stated_input.StatedInput(stdin="3\n1 2 3\n")),
    ("inline numeric value", "sum them. Input: 3 4", stated_input.StatedInput(stdin="3 4\n")),
    ("inline code value", "reverse it. Input: `hello world`", stated_input.StatedInput(stdin="hello world\n")),
    ("echo piped in", "it should work like `echo 5 | python3 main.py`", stated_input.StatedInput(stdin="5\n")),
    ("program arguments", "run as `python3 main.py data.txt 3`", stated_input.StatedInput(arguments=("data.txt", "3"))),
    ("compiled program arguments", "usage:\n```\n$ ./sum 2 3\n```", stated_input.StatedInput(arguments=("2", "3"))),
    ("prose after the label", "Input: a list of numbers from the user", None),
    ("no input at all", WITHOUT_INPUT, None),
    ("empty question", "", None),
)
MISREAD_BEFORE = (
    ("a label in brackets", "Add two numbers (input: 3 4)", stated_input.StatedInput(stdin="3 4\n")),
    ("an inline value before an output label", "Square it. Input: 5 Output: 25", stated_input.StatedInput(stdin="5\n")),
    ("echo without a newline", "Write square.py so `echo -n 5 | python3 square.py` prints 25",
     stated_input.StatedInput(stdin="5\n")),
    ("echo with escapes", 'Write add.py so `echo -e "1\\n2" | python3 add.py` prints 3', stated_input.StatedInput(stdin="1\n2\n")),
    ("printf with a format", "Write square.py so `printf '%s\\n' 5 | python3 square.py` prints 25", stated_input.StatedInput(stdin="5\n")),
    ("printf with two values", "Write add, try `printf '%d %d\\n' 3 4 | ./add`", stated_input.StatedInput(stdin="3 4\n")),
    ("a label in brackets mid-sentence", "Write a script that squares a number (input: 5) and prints it",
     stated_input.StatedInput(stdin="5\n")),
    ("a bare run before the command", "Run `./price.sh '$5.00' $HOME`", stated_input.StatedInput(arguments=("$5.00", "$HOME"))),
    ("a here-string is stdin", "Write square.py so `python3 square.py <<< 5` prints 25", stated_input.StatedInput(stdin="5\n")),
    ("stdin from a file is not input", "Write square.py so `python3 square.py < numbers.txt` works", None),
    ("an output redirect ends the arguments", "Write report.py, run as `python3 report.py data.csv > out.txt`",
     stated_input.StatedInput(arguments=("data.csv",))),
    ("a pipe ends the arguments", "Write list.py, run as `python3 list.py /tmp | head -3`", stated_input.StatedInput(arguments=("/tmp",))),
    ("an unrelated command of the user's", "My `./deploy.sh --dry-run` prints nothing. Write a python script that counts lines on stdin.",
     None),
    ("the user's own server command", "I start the site with `python3 manage.py runserver`. Write a script that reads a name with input()",
     None),
)
READ_LITERALLY = (
    ("a lead-in before another program, then the request",
     "The deploy is broken. Run `./deploy.sh --dry-run` and you will see. Write a python script that counts the lines on stdin.",
     None),
    ("an example of another program, then the request",
     "For example `./configure --prefix=/usr` fails on my box. Write a C program that prints its first argument.", None),
    ("a usage line with a one-letter placeholder", "Usage: `python3 square.py N`", None),
    ("a usage line with a placeholder in angle brackets", "Usage: `python3 square.py <number>`", None),
    ("echo without -e keeps the backslash", 'For example `echo "1\\n2" | python3 add.py`', stated_input.StatedInput(stdin="1\\n2\n")),
    ("a request, then a lead-in for the new program", "Write add.py. For example `python3 add.py 3 4` prints 7",
     stated_input.StatedInput(arguments=("3", "4"))),
)

SQUARE_ANSWER = "```python\nimport sys\nprint(int(sys.argv[1]) ** 2)\n```"
SQUARE_OF_5 = stated_input.StatedInput(arguments=("5",))
EXAMPLE_BEFORE_THE_REQUEST = (
    "Write a python script that squares its argument. Example: `python3 square.py 5` prints 25. "
    "Then create a second script that cubes it.",
    "Write a python script that squares its argument. For example `python3 square.py 5` should print 25. "
    "Also give me a one-liner that does the same.",
    "For example `python3 square.py 5` should print 25. Write the script.",
    "I want it to work like this: `python3 square.py 5` prints 25. Please write the code.",
    "Example: `python3 square.py 5` gives 25. Can you write that program in python?",
)

SQUARE_IN_NODE = "```javascript\nconsole.log(Number(process.argv[2]) ** 2);\n```"
ANOTHER_PROGRAMS_COMMAND = (
    ("My migration fails. Run `python3 manage.py migrate` and you will see. Write a python script that squares its argument.",
     SQUARE_ANSWER),
    ("For example `python3 old_tool.py --legacy` crashes on my box. Write a python script that squares its argument.", SQUARE_ANSWER),
    ("We call the build like this: `python3 build.py --release`. Now write a python script that squares its argument.",
     SQUARE_ANSWER),
    ("Our CI runs e.g. `node lint.js --fix`. Write a node script that squares its argument.", SQUARE_IN_NODE),
    ("For example `python3 train.py --epochs 3` takes an hour. Write a python script that prints the time.",
     "```python\nimport time\nprint(time.time())\n```"),
    ("Example: `python3 square.py 5` gives 25. Write that in javascript.", SQUARE_IN_NODE),
)


class ReadsTheInputFromTheQuestion(unittest.TestCase):
    def test_a_command_for_another_program_is_not_the_new_scripts_input(self):
        for question, answer in ANOTHER_PROGRAMS_COMMAND:
            with self.subTest(question=question):
                self.assertIsNone(stated_input.stated_input(question, answer))

    def test_an_example_of_the_program_before_the_write_request_is_read(self):
        for question in EXAMPLE_BEFORE_THE_REQUEST:
            with self.subTest(question=question):
                self.assertEqual(SQUARE_OF_5, stated_input.stated_input(question, SQUARE_ANSWER))

    def test_other_programs_placeholders_and_plain_echo_are_read_as_written(self):
        for case, question, expected in READ_LITERALLY:
            with self.subTest(case=case):
                self.assertEqual(expected, stated_input.stated_input(question))

    def test_shapes_that_were_misread(self):
        for case, question, expected in MISREAD_BEFORE:
            with self.subTest(case=case):
                self.assertEqual(expected, stated_input.stated_input(question))

    def test_each_way_of_stating_input(self):
        for case, question, expected in STATED_INPUTS:
            with self.subTest(case=case):
                self.assertEqual(expected, stated_input.stated_input(question))


class SaysItNeedsInput(unittest.TestCase):
    def report(self, verdict):
        return code_sandbox.Report([code_sandbox.Unit("python", "x", "", "Python")], [verdict], 1.0)

    def test_the_summary_says_it_could_not_be_tested_without_input(self):
        summary = code_sandbox.summary(self.report(NEEDS_INPUT))
        self.assertIn("couldn't be tested without input", summary)
        self.assertNotIn("ran without errors", summary)
        self.assertNotIn("fails", summary)

    def test_needing_input_after_the_questions_input_says_that_input_was_given(self):
        summary = code_sandbox.summary(self.report({**NEEDS_INPUT, code_sandbox.WITH_STATED_INPUT: True}))
        self.assertIn("couldn't be tested with the input from the question", summary)
        self.assertNotIn("without input", summary)

    def test_needing_input_is_not_a_failure_to_rewrite(self):
        self.assertEqual([], self.report(NEEDS_INPUT).failures())

    def test_a_failure_on_made_up_input_shows_the_error_not_the_note(self):
        summary = code_sandbox.summary(self.report(FAILED_ON_MADE_UP_INPUT))
        self.assertIn("ValueError: invalid literal for int()", summary)
        self.assertNotIn("Run again with made-up input", summary)

    def test_a_pass_on_the_stated_input_says_so(self):
        verdict = {**PASSED, code_sandbox.WITH_STATED_INPUT: True}
        self.assertIn("ran without errors on the input from the question", code_sandbox.summary(self.report(verdict)))


def sent_fields(unit):
    opened = []

    def answered(request, timeout):
        opened.append(json.loads(request.data))
        return io.BytesIO(json.dumps(PASSED).encode())

    with unittest.mock.patch.object(code_sandbox.urllib.request, "urlopen", side_effect=answered):
        code_sandbox.post_check(RUNNER, unit, 5)
    return opened[0]


class SendsTheInputToTheRunner(unittest.TestCase):
    def test_stdin_and_arguments_go_in_the_request(self):
        fields = sent_fields(code_sandbox.Unit("python", "print(1)\n", "", "Python", stdin="3 4\n", arguments=("a", "b")))
        self.assertEqual(("3 4\n", ["a", "b"]), (fields["stdin"], fields["args"]))

    def test_no_input_sends_neither_field(self):
        fields = sent_fields(code_sandbox.Unit("python", "print(1)\n", "", "Python"))
        self.assertNotIn("stdin", fields)
        self.assertNotIn("args", fields)


class RerunsWithTheStatedInput(unittest.TestCase):
    def test_a_rerun_that_passes_is_reported_as_run_on_the_input(self):
        outcome, engine, checks = run_coding_turn([said(READS_TWO_NUMBERS)], [NEEDS_INPUT, PASSED], question=WITH_INPUT)
        self.assertEqual(2, len(checks))
        self.assertEqual(("", "3 4\n"), (checks[0].stdin, checks[1].stdin))
        self.assertEqual(1, len(engine.chat_bodies))
        self.assertIn("ran without errors on the input from the question", outcome["answer"])

    def test_a_rerun_that_fails_is_sent_back_for_a_rewrite_naming_the_input(self):
        outcome, engine, checks = run_coding_turn([said(READS_TWO_NUMBERS), said(READS_TWO_NUMBERS)],
                                                  [NEEDS_INPUT, FAILED, NEEDS_INPUT, PASSED], question=WITH_INPUT)
        self.assertEqual(2, len(engine.chat_bodies))
        self.assertIn("with the input from the question", last_user_note(engine.chat_bodies[1]))
        self.assertIn("ran without errors on the input from the question", outcome["answer"])

    def test_a_failed_run_is_tried_on_the_stated_input_before_it_stands(self):
        outcome, engine, checks = run_coding_turn([said(READS_TWO_NUMBERS)] * 3, [FAILED_ON_MADE_UP_INPUT, PASSED] * 3,
                                                  question=WITH_INPUT)
        self.assertEqual(1, len(engine.chat_bodies))
        self.assertEqual(("", "3 4\n"), tuple(check.stdin for check in checks))
        self.assertIn("ran without errors on the input from the question", outcome["answer"])
        self.assertNotIn("fails", outcome["answer"])

    def test_a_failure_is_never_replaced_by_needs_input_on_the_stated_input(self):
        outcome, engine, checks = run_coding_turn([said(READS_TWO_NUMBERS)] * 3, [FAILED_ON_MADE_UP_INPUT, NEEDS_INPUT] * 3,
                                                  question=WITH_INPUT)
        self.assertEqual("3 4\n", checks[1].stdin)
        self.assertGreater(len(engine.chat_bodies), 1)
        self.assertIn("ValueError: invalid literal for int()", outcome["answer"])
        self.assertNotIn("couldn't be tested", outcome["answer"])

    def test_a_failure_before_the_run_is_not_tried_again(self):
        _, _, checks = run_coding_turn([said(READS_TWO_NUMBERS), said(READS_TWO_NUMBERS), said(READS_TWO_NUMBERS)],
                                       [FAILED_BEFORE_RUNNING, FAILED_BEFORE_RUNNING, FAILED_BEFORE_RUNNING], question=WITH_INPUT)
        self.assertEqual(["", "", ""], [check.stdin for check in checks])

    def test_without_stated_input_there_is_no_rerun_and_no_rewrite(self):
        outcome, engine, checks = run_coding_turn([said(READS_TWO_NUMBERS)], [NEEDS_INPUT], question=WITHOUT_INPUT)
        self.assertEqual(1, len(checks))
        self.assertEqual(1, len(engine.chat_bodies))
        self.assertIn("couldn't be tested without input", outcome["answer"])
        self.assertNotIn("ran without errors", outcome["answer"])

    def test_a_runner_that_drops_out_on_the_rerun_keeps_needs_input(self):
        dropped = urllib.error.URLError("connection refused")
        outcome, engine, checks = run_coding_turn([said(READS_TWO_NUMBERS)], [NEEDS_INPUT, dropped], question=WITH_INPUT)
        self.assertEqual(2, len(checks))
        self.assertIn("couldn't be tested without input", outcome["answer"])

    def test_no_rerun_when_too_little_time_is_left(self):
        unit = code_sandbox.Unit("python", "x", "", "Python")
        with unittest.mock.patch.object(settings, "CODE_RUNNER_URL", RUNNER), \
                unittest.mock.patch.object(code_sandbox, "post_check") as post:
            verdicts = code_sandbox.with_stated_input([unit], [NEEDS_INPUT], WITH_INPUT, code_sandbox.MIN_RERUN_SECONDS - 1)
        post.assert_not_called()
        self.assertEqual([NEEDS_INPUT], verdicts)


if __name__ == "__main__":
    unittest.main()

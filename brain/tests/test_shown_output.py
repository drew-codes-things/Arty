import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

from test_code_sandbox import PASSED, fence, run_coding_turn  # noqa: E402
from test_review_tonight import last_user_note, said  # noqa: E402

import code_sandbox  # noqa: E402
import shown_output  # noqa: E402

T_STRING_QUESTION = "How do I use Python 3.14 t-strings to build an HTML snippet? Show the code."
T_STRING_CODE = ("from string.templatelib import Template\n\nuser_name = \"Robin\"\n"
                 "html_snippet = t\"<div><strong>{user_name}</strong></div>\"\nresult = str(html_snippet)\nprint(result)")
CLAIMED_HTML = "<div><strong>Robin</strong></div>"
REAL_T_STRING = ("Template(strings=('<div><strong>', '</strong></div>'), interpolations=(Interpolation('Robin', 'user_name', "
                 "None, ''),))\n")
WRONG_CLAIM = f"Here it is:\n\n{fence('python', T_STRING_CODE)}\n\nThis will output:\n\n{fence('html', CLAIMED_HTML)}\n\nThat's it."
HONEST_CLAIM = (f"Here it is:\n\n{fence('python', T_STRING_CODE)}\n\nThis will output:\n\n"
                f"{fence('', REAL_T_STRING.strip())}\n\nThat's it.")
JOIN_CODE = "join_by() {\n  local IFS=\"$1\"\n  shift\n  echo \"$*\"\n}"
JOIN_ANSWER = (f"Here's the function:\n\n{fence('bash', JOIN_CODE)}\n\nWhen I ran it with the arguments `, a b c`, it printed:\n"
               f"{fence('', 'a,b,c')}\n\nAnd when I ran it with the arguments `' - ' x y`, it printed:\n{fence('', 'x - y')}\n")
JOIN_QUESTION = "Write a bash function join_by that joins its remaining arguments using its first argument as the separator."


def ran(stdout):
    return {**PASSED, "stdout": stdout, "tool": "Python 3.14"}


def bash_ran(stdout):
    return {"status": "passed", "checked": ["syntax", "lint", "run"], "notes": [], "tool": "bash and ShellCheck",
            "seconds": 0.2, "stdout": stdout}


class FindsShownOutput(unittest.TestCase):
    def test_a_block_after_an_output_lead_is_a_claim_and_not_code(self):
        claims = code_sandbox.output_claims(WRONG_CLAIM)
        self.assertEqual([CLAIMED_HTML + "\n"], [claim.code for claim in claims])
        self.assertEqual(["python"], [unit.language for unit in code_sandbox.units_in(WRONG_CLAIM)])

    def test_bold_and_plain_leads_count(self):
        for lead in ("**Output:**", "Output:", "When I ran it, it printed:", "You'll see:", "Example output:"):
            answer = f"{fence('python', 'print(1)')}\n\n{lead}\n{fence('', '1')}"
            self.assertEqual(1, len(code_sandbox.output_claims(answer)), lead)

    def test_blocks_that_are_not_shown_output_are_not_claims(self):
        answers = (
            f"Output:\n{fence('', '1')}\n\n{fence('python', 'print(1)')}",
            f"{fence('python', 'print(1)')}\n\nHere's the code that prints the total:\n{fence('python', 'print(2)')}",
            f"{fence('python', 'print(1)')}\n\nIt prints:\n{fence('', 'def total(xs):\\n    return sum(xs)')}",
            f"{fence('python', 'print(1)')}\n\nInstall it with:\n{fence('', 'pip install x')}",
        )
        for answer in answers:
            self.assertEqual([], code_sandbox.output_claims(answer), answer)

    def test_output_that_matches_or_shows_part_is_fine(self):
        self.assertTrue(shown_output.is_shown_by("a,b,c\nx - y\n", "a,b,c\nx - y\n"))
        self.assertTrue(shown_output.is_shown_by("x - y", "a,b,c\nx - y  \n\n"))
        self.assertTrue(shown_output.is_shown_by("$ python main.py\n5400", "5400\n"))
        self.assertFalse(shown_output.is_shown_by("x - y", "x y\n"))
        self.assertFalse(shown_output.is_shown_by(CLAIMED_HTML, REAL_T_STRING))

    def test_output_that_depends_on_time_randomness_input_or_hash_order_is_never_compared(self):
        for code in ("import random\nprint(random.randint(1, 6))", "from datetime import datetime\nprint(datetime.now())",
                     "name = input()\nprint(name)", "use std::collections::HashMap;\nfn main() { let m: HashMap<i32, i32> = HashMap::new(); }",
                     "console.log(Math.random())", "print(set(['a', 'b']))", "echo $RANDOM"):
            self.assertFalse(shown_output.is_stable(code), code)
        self.assertTrue(shown_output.is_stable(T_STRING_CODE))

    def test_shell_calls_come_from_the_text_before_the_shown_output(self):
        self.assertEqual(["join_by , a b c"], shown_output.shell_calls("When I ran it with the arguments `, a b c`, it printed:", JOIN_CODE))
        self.assertEqual(["join_by , a b c", "join_by ' - ' x y"],
                         shown_output.shell_calls("Run `join_by , a b c` and `join_by ' - ' x y` and it prints:", JOIN_CODE))
        self.assertEqual([], shown_output.shell_calls("With `ls -l` it prints:", JOIN_CODE))


class ComparesShownOutputWithTheRealRun(unittest.TestCase):
    def test_a_wrong_claim_is_sent_back_once_with_both_outputs(self):
        outcome, ollama, checks = run_coding_turn([said(WRONG_CLAIM), said(HONEST_CLAIM)], [ran(REAL_T_STRING), ran(REAL_T_STRING)],
                                                  question=T_STRING_QUESTION)
        self.assertEqual(2, len(ollama.chat_bodies))
        note = last_user_note(ollama.chat_bodies[1])
        self.assertIn(CLAIMED_HTML, note)
        self.assertIn("Interpolation('Robin'", note)
        self.assertTrue(outcome["answer"].startswith(HONEST_CLAIM))
        self.assertNotIn("really printed", outcome["answer"])
        self.assertEqual(["python", "python"], [unit.language for unit in checks])

    def test_a_claim_still_wrong_after_the_rewrite_is_replaced_with_the_real_output(self):
        outcome, ollama, _ = run_coding_turn([said(WRONG_CLAIM), said(WRONG_CLAIM), said(HONEST_CLAIM)],
                                             [ran(REAL_T_STRING), ran(REAL_T_STRING)], question=T_STRING_QUESTION)
        self.assertEqual(2, len(ollama.chat_bodies))
        self.assertNotIn(CLAIMED_HTML, outcome["answer"])
        self.assertIn(REAL_T_STRING.strip(), outcome["answer"])
        self.assertIn("```html\nTemplate(strings=", outcome["answer"])
        self.assertIn("the output shown is what it really printed in the sandbox, not what I first wrote", outcome["answer"])

    def test_a_matching_claim_is_answered_straight_away(self):
        outcome, ollama, _ = run_coding_turn([said(HONEST_CLAIM)], [ran(REAL_T_STRING)], question=T_STRING_QUESTION)
        self.assertEqual(1, len(ollama.chat_bodies))
        self.assertTrue(outcome["answer"].startswith(HONEST_CLAIM))

    def test_a_runner_that_gives_no_stdout_changes_nothing(self):
        outcome, ollama, _ = run_coding_turn([said(WRONG_CLAIM)], [PASSED], question=T_STRING_QUESTION)
        self.assertEqual(1, len(ollama.chat_bodies))
        self.assertIn(CLAIMED_HTML, outcome["answer"])

    def test_random_output_is_not_flagged(self):
        answer = f"{fence('python', 'import random\nprint(random.randint(1, 6))')}\n\nOutput:\n{fence('', '4')}"
        outcome, ollama, _ = run_coding_turn([said(answer)], [ran("2\n")], question="write a python dice roller")
        self.assertEqual(1, len(ollama.chat_bodies))
        self.assertIn("```\n4\n```", outcome["answer"])

    def test_shell_function_calls_named_in_the_answer_are_run_and_compared(self):
        fixed = JOIN_ANSWER.replace("x - y", "x y")
        outcome, ollama, checks = run_coding_turn(
            [said(JOIN_ANSWER), said(fixed)],
            [bash_ran(""), bash_ran("a,b,c\n"), bash_ran("x y\n"), bash_ran(""), bash_ran("a,b,c\n"), bash_ran("x y\n")],
            question=JOIN_QUESTION)
        self.assertEqual(2, len(ollama.chat_bodies))
        self.assertTrue(checks[2].code.endswith("join_by ' - ' x y\n"))
        note = last_user_note(ollama.chat_bodies[1])
        self.assertIn("x - y", note)
        self.assertIn("x y", note)
        self.assertTrue(outcome["answer"].startswith(fixed))


INLINE_CODE = "join_by() {\n  local separator=\"$1\"\n  shift\n  printf \"%s\" \"${@// /$separator}\"\n}"
INLINE_ANSWER = (f"Here it is:\n\n{fence('bash', INLINE_CODE)}\n\nFor example:\n\n- `join_by , a b c` will output `a,b,c`\n"
                 "- `join_by ' - ' x y` will output `x - y`\n")


class ComparesInlineShellClaims(unittest.TestCase):
    def test_inline_claims_about_shell_calls_are_found_outside_code(self):
        claims = code_sandbox.inline_shell_claims(INLINE_ANSWER)
        self.assertEqual(["a,b,c\n", "x - y\n"], [claim.code for claim in claims])
        self.assertEqual([], code_sandbox.inline_shell_claims(fence("bash", "# `ls` prints `a`\necho hi")))

    def test_wrong_inline_claims_are_sent_back_then_replaced(self):
        outcome, ollama, checks = run_coding_turn(
            [said(INLINE_ANSWER), said(INLINE_ANSWER)],
            [bash_ran(""), bash_ran("abc"), bash_ran("xy"), bash_ran(""), bash_ran("abc"), bash_ran("xy")],
            question=JOIN_QUESTION)
        self.assertEqual(2, len(ollama.chat_bodies))
        self.assertIn("a,b,c", last_user_note(ollama.chat_bodies[1]))
        self.assertTrue(checks[1].code.endswith("join_by , a b c\n"))
        self.assertIn("- `join_by , a b c` will output `abc`", outcome["answer"])
        self.assertIn("- `join_by ' - ' x y` will output `xy`", outcome["answer"])
        self.assertIn("not what I first wrote", outcome["answer"])

    def test_right_inline_claims_change_nothing(self):
        outcome, ollama, _ = run_coding_turn([said(INLINE_ANSWER)], [bash_ran(""), bash_ran("a,b,c\n"), bash_ran("x - y\n")],
                                             question=JOIN_QUESTION)
        self.assertEqual(1, len(ollama.chat_bodies))
        self.assertIn("will output `x - y`", outcome["answer"])


if __name__ == "__main__":
    unittest.main()

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
for key in ("BRAIN_API_KEY", "MCPO_API_KEY"):
    os.environ.setdefault(key, "test")

from test_review_tonight import FakeHub, FakeEngine, last_user_note, said  # noqa: E402

import brain  # noqa: E402
import code_sandbox  # noqa: E402
import connections  # noqa: E402
import example_checks  # noqa: E402
import settings  # noqa: E402

RUNNER = "http://runner.test:8790"
THINKING_MODEL = {"name": "m", "thinking": True, "tools": True, "billions": 0.0}
QUESTION = ("Write a C function int is_even(int n) that returns 1 for even numbers and 0 for odd ones, so is_even(4) "
            "gives 1 and is_even(7) gives 0. Include a main.")
WRONG = "```c\n#include <stdio.h>\n\nint is_even(int n) {\n    return n % 2;\n}\n\nint main(void) {\n    printf(\"%d\\n\", is_even(4));\n    return 0;\n}\n```"
RIGHT = WRONG.replace("return n % 2;", "return n % 2 == 0;")
CHECKS = ("```c\n#include <stdio.h>\nint main(void) {\n    printf(is_even(4) == 1 ? \"CHECK PASS: is_even(4)\\n\" : "
          "\"CHECK FAIL: is_even(4): got 0, want 1\\n\");\n    return 0;\n}\n```")
PASSED = {"status": "passed", "checked": ["compile", "run"], "notes": [], "tool": "gcc", "seconds": 0.3, "stdout": "0\n"}
CHECKS_FAIL = {**PASSED, "stdout": "CHECK FAIL: is_even(4): got 0, want 1\nCHECK PASS: is_even(7)\n"}
CHECKS_PASS = {**PASSED, "stdout": "CHECK PASS: is_even(4)\nCHECK PASS: is_even(7)\n"}
CHECKS_BROKEN = {"status": "failed", "step": "compile", "checked": [], "notes": [], "tool": "gcc", "seconds": 0.2,
                 "problem": "main.c:14:5: error: redefinition of 'main'"}
CRASHED = {"status": "failed", "step": "run", "checked": ["compile"], "notes": [], "tool": "gcc", "seconds": 0.2,
           "problem": "==12==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x502\nREAD of size 1"}


def run_turn(replies, verdicts, config=None, model=None):
    engine = FakeEngine(replies)
    verdicts = iter(verdicts)
    checked = []
    state = {"messages": [{"role": "user", "content": QUESTION}], "context": {}}

    def answer(url, unit, timeout):
        checked.append(unit)
        return next(verdicts)

    with unittest.mock.patch.object(connections, "hub", FakeHub()), \
            unittest.mock.patch.object(connections, "http_json", side_effect=engine), \
            unittest.mock.patch.object(brain.models, "budget_for", return_value=0), \
            unittest.mock.patch.object(brain.models, "find", return_value=model), \
            unittest.mock.patch("agent_loop.system_prompt", return_value=""), \
            unittest.mock.patch("agent_loop.current_context", return_value=""), \
            unittest.mock.patch.object(brain.policy, "select_tools", side_effect=lambda tools, *args, **kwargs: tools), \
            unittest.mock.patch.object(settings, "CODE_RUNNER_URL", RUNNER), \
            unittest.mock.patch.object(code_sandbox, "post_check", side_effect=answer):
        outcome = brain.run_agent(config or {"model": "m", "example_checks": ["c"]}, state, run=brain.AgentRun())
    return outcome, engine, checked


class JoinsTheChecksToTheCode(unittest.TestCase):
    def test_c_code_keeps_its_functions_and_its_main_is_renamed(self):
        joined = example_checks.with_checks("c", "int f(void) { return 1; }\nint main(void) { return f(); }\n",
                                            "int main(void) { return f() - 1; }\n")
        self.assertEqual(1, joined.count("int main("))
        self.assertIn("int shown_main(void) { return f(); }", joined)

    def test_c_checks_get_macros_that_print_their_check_lines(self):
        joined = example_checks.with_checks("c", "int f(void) { return 1; }\n", "int main(void) { CHECK_INT(f(), 1); return 0; }\n")
        self.assertLess(joined.index("int f(void)"), joined.index("#define CHECK_INT("))
        self.assertLess(joined.index("#define CHECK_STR("), joined.index("int main(void)"))
        prompt = example_checks.writer_prompt("write f", code_sandbox.Unit("c", "int f(void);\n", "", "C"))
        self.assertIn("CHECK_INT(actual, expected)", prompt)
        self.assertIn("CHECK_STR(actual, expected)", prompt)

    def test_c_checks_lose_their_own_copies_of_the_macros_and_of_the_code(self):
        code = "#include <string.h>\nint twice(int n) {\n    return 2 * n;\n}\nint main(void) { return twice(1); }\n"
        checks = ("#include <stdio.h>\n#define CHECK_INT(actual, expected) do { \\\n    printf(\"%s\", #input); \\\n} while (0)\n"
                  "int twice(int n) {\n    if (n) { return 2 * n; }\n    return 0;\n}\n"
                  "int main(void) {\n    CHECK_INT(twice(2), 4);\n    return 0;\n}\n")
        joined = example_checks.with_checks("c", code, checks)
        self.assertEqual(1, joined.count("#define CHECK_INT("))
        self.assertNotIn("#input", joined)
        self.assertEqual(1, joined.count("int twice(int n) {"))
        self.assertIn("int main(void) {\n    CHECK_INT(twice(2), 4);", joined)

    def test_rust_keeps_one_copy_of_each_use_line(self):
        code = "use std::collections::HashMap;\n\nfn count() -> HashMap<String, usize> { HashMap::new() }\nfn main() {}\n"
        checks = "use std::collections::HashMap;\nuse std::fmt;\n\nfn main() { let _: HashMap<String, usize> = count(); }\n"
        joined = example_checks.with_checks("rust", code, checks)
        self.assertEqual(1, joined.count("use std::collections::HashMap;"))
        self.assertIn("use std::fmt;", joined)
        self.assertEqual(1, len(example_checks.RUST_MAIN.findall(joined)))
        self.assertTrue(joined.startswith("#![allow("))

    def test_go_gets_one_package_line_and_one_import_block(self):
        code = "package main\n\nimport (\n\t\"fmt\"\n\t\"sort\"\n)\n\nfunc Top() []int { sort.Ints(nil); return nil }\n\nfunc main() {\n\tfmt.Println(Top())\n}\n"
        checks = "import (\n\t\"fmt\"\n\t\"os\"\n)\n\nfunc main() {\n\tfmt.Println(len(Top()))\n\tos.Exit(0)\n}\n"
        joined = example_checks.with_checks("go", code, checks)
        self.assertEqual(1, joined.count("package main"))
        self.assertEqual(1, joined.count("import ("))
        self.assertEqual(['"fmt"', '"os"', '"sort"'], sorted(line.strip() for line in joined.split("import (")[1].split(")")[0].split()))
        self.assertIn("func shownMain()", joined)
        self.assertEqual(1, len(example_checks.GO_MAIN.findall(joined)))

    def test_java_imports_go_first_and_the_checks_class_is_the_public_one(self):
        code = "import java.util.List;\n\npublic class Solution {\n    static int one() { return 1; }\n}\n"
        checks = "import java.util.ArrayList;\nclass Checks {\n    public static void main(String[] args) { Solution.one(); }\n}\n"
        joined = example_checks.with_checks("java", code, checks)
        self.assertTrue(joined.startswith("import java.util.List;\nimport java.util.ArrayList;\n"))
        self.assertIn("\nclass Solution {", joined)
        self.assertIn("public class Checks {", joined)

    def test_scripts_are_followed_by_their_checks(self):
        self.assertEqual("def f():\n    return 1\n\nprint(f())\n",
                         example_checks.with_checks("python", "def f():\n    return 1\n", "print(f())\n"))


C_RESULTS = ("#include <limits.h>\ndouble average(int a, int b) { return (a + b) / 2; }\n"
             "unsigned long long big(void) { return ULLONG_MAX; }\nconst char *two_lines(void) { return \"a\\nb\"; }\n"
             "int main(void) { return 0; }\n")
C_RESULT_CHECKS = ("int main(void) {\n    CHECK_INT(average(1, 2), 1.5);\n    CHECK_NUM(average(3, 4), 3.5);\n"
                   "    CHECK_NUM(average(2, 4), 3.0);\n    CHECK_INT(big(), -1);\n    CHECK_STR(two_lines(), \"a b\");\n"
                   "    return 0;\n}\n")

C_ODD_RESULTS = ("#include <math.h>\n#include <stddef.h>\nstatic int value = 3;\n"
                 "int *found(int wanted) { return wanted ? &value : NULL; }\n"
                 "double endless(void) { return INFINITY; }\ndouble undefined_number(void) { return NAN; }\n"
                 "int main(void) { return 0; }\n")
C_ODD_RESULT_CHECKS = ("int main(void) {\n    CHECK_INT(found(0), NULL);\n    CHECK_INT(found(1), NULL);\n"
                       "    CHECK_NUM(endless(), INFINITY);\n    CHECK_NUM(undefined_number(), NAN);\n"
                       "    CHECK_NUM(endless(), 1.0);\n    return 0;\n}\n")

C_EDGE_RESULTS = ("#include <math.h>\nstruct flags { unsigned level : 3; };\nstatic struct flags state = {5};\n"
                  "double shortest(int reachable) { return reachable ? 4.0 : 1e300; }\n"
                  "__int128 wide(void) { return 5; }\nint main(void) { return 0; }\n")
C_EDGE_RESULT_CHECKS = ("int main(void) {\n    CHECK_NUM(shortest(0), INFINITY);\n    CHECK_NUM(5.0, INFINITY);\n"
                        "    CHECK_NUM(-INFINITY, INFINITY);\n    CHECK_NUM(INFINITY, -INFINITY);\n"
                        "    CHECK_NUM(shortest(1) * INFINITY, INFINITY);\n    CHECK_INT(state.level, 5);\n"
                        "    CHECK_INT(state.level, 4);\n    CHECK_INT(wide(), 5);\n    return 0;\n}\n")


def compiled_and_run(code: str, checks: str) -> str:
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "main.c"
        source.write_text(example_checks.with_checks("c", code, checks))
        subprocess.run(["gcc", "-std=gnu17", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(Path(folder) / "main"),
                        "-lm"], check=True, capture_output=True, text=True, timeout=60)
        return subprocess.run([str(Path(folder) / "main")], capture_output=True, text=True, timeout=10).stdout


@unittest.skipUnless(shutil.which("gcc"), "needs gcc")
class TheCMacrosJudgeResultsExactly(unittest.TestCase):
    def test_a_wanted_infinity_needs_infinity_and_bit_fields_and_wide_integers_build(self):
        printed = compiled_and_run(C_EDGE_RESULTS, C_EDGE_RESULT_CHECKS)
        self.assertEqual(["FAIL", "FAIL", "FAIL", "FAIL", "PASS", "PASS", "FAIL", "PASS"],
                         [line.split(":")[0].split()[-1] for line in printed.splitlines()], printed)

    def test_a_pointer_to_a_struct_that_is_only_declared_builds(self):
        code = ("#include <stddef.h>\n#include <stdbool.h>\nstruct handle;\nstatic char slot;\n"
                "struct handle *open_handle(int ok) { return ok ? (struct handle *)&slot : NULL; }\n"
                "unsigned count(void) { return 7; }\nbool ready(void) { return true; }\nchar letter(void) { return 'a'; }\n"
                "int main(void) { return 0; }\n")
        checks = ("int main(void) {\n    CHECK_INT(open_handle(0), NULL);\n    CHECK_INT(open_handle(1), NULL);\n"
                  "    CHECK_INT(count(), 7);\n    CHECK_INT(ready(), true);\n    CHECK_INT(letter(), 'a');\n    return 0;\n}\n")
        printed = compiled_and_run(code, checks)
        self.assertEqual(["PASS", "FAIL", "PASS", "PASS", "PASS"],
                         [line.split(":")[0].split()[-1] for line in printed.splitlines()], printed)

    def test_fractions_big_numbers_and_newlines_are_judged_right(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "main.c"
            source.write_text(example_checks.with_checks("c", C_RESULTS, C_RESULT_CHECKS))
            subprocess.run(["gcc", "-std=gnu17", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(Path(folder) / "main"),
                            "-lm"], check=True, capture_output=True, text=True, timeout=60)
            printed = subprocess.run([str(Path(folder) / "main")], capture_output=True, text=True, timeout=10).stdout
        lines = printed.splitlines()
        self.assertEqual(5, len(lines), printed)
        self.assertEqual(["FAIL", "FAIL", "PASS", "FAIL", "FAIL"], [line.split(":")[0].split()[-1] for line in lines])
        self.assertIn('got "a\\nb", want "a b"', lines[4])
        outcome = example_checks.outcome_of({"status": "passed", "stdout": printed}, "c", "average(1, 2) gives 1.5")
        self.assertEqual((1, 4), (outcome.passed, len(outcome.failures) + outcome.unsure))

    def test_pointers_infinity_and_nan_are_judged_right(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "main.c"
            source.write_text(example_checks.with_checks("c", C_ODD_RESULTS, C_ODD_RESULT_CHECKS))
            subprocess.run(["gcc", "-std=gnu17", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(Path(folder) / "main"),
                            "-lm"], check=True, capture_output=True, text=True, timeout=60)
            printed = subprocess.run([str(Path(folder) / "main")], capture_output=True, text=True, timeout=10).stdout
        self.assertEqual(["PASS", "FAIL", "PASS", "PASS", "FAIL"],
                         [line.split(":")[0].split()[-1] for line in printed.splitlines()], printed)


class ReadsTheWrittenChecks(unittest.TestCase):
    def test_the_fenced_program_is_taken(self):
        self.assertEqual("print(1)\n", example_checks.checks_in("Here:\n```python\nprint(1)\n```\n"))

    def test_the_writer_is_asked_for_a_bounded_number_of_checks(self):
        prompt = example_checks.writer_prompt("write f", code_sandbox.Unit("python", "def f():\n    return 1\n", "", "Python"))
        self.assertIn(f"at most {example_checks.MOST_CHECKS} checks", prompt)

    def test_none_or_no_code_means_no_checks(self):
        self.assertEqual("", example_checks.checks_in("NONE"))
        self.assertEqual("", example_checks.checks_in("I can't test this."))

    def test_the_prompt_carries_the_question_the_code_and_the_language_rules(self):
        unit = code_sandbox.Unit("c", "int f(void);\n", "", "C")
        prompt = example_checks.writer_prompt("write f", unit)
        self.assertIn("write f", prompt)
        self.assertIn("```c\nint f(void);\n```", prompt)
        self.assertIn("int main(void)", prompt)
        self.assertIn("CHECK FAIL:", prompt)


class JudgesTheChecks(unittest.TestCase):
    def test_check_lines_are_counted(self):
        outcome = example_checks.outcome_of(CHECKS_FAIL, "c", QUESTION)
        self.assertEqual((1, ["is_even(4): got 0, want 1"]), (outcome.passed, outcome.failures))
        self.assertTrue(outcome.is_failing)

    def test_only_results_the_question_states_count_as_failures(self):
        verdict = {**PASSED, "stdout": "CHECK FAIL: parse(''): got 0, want None\nCHECK FAIL: join(',', 'a', 'b'): got 'a b', "
                                       "want 'a,b'\nCHECK PASS: parse('1h')\n"}
        outcome = example_checks.outcome_of(verdict, "python", "parse('1h') gives 3600 and join(',', 'a', 'b') gives a,b")
        self.assertEqual((1, ["join(',', 'a', 'b'): got 'a b', want 'a,b'"], 1),
                         (outcome.passed, outcome.failures, outcome.unsure))

    def test_a_number_only_counts_as_stated_next_to_the_same_call(self):
        question = 'Write count_vowels(text). count_vowels("hello") gives 2. Text is up to 100 characters.'
        for failure, stated in (('count_vowels("hello"): got 1, want 2', True), ('count_vowels("sky"): got 0, want 1', False),
                                ('count_vowels("aeiou"): got 5, want 10', False), ('count_vowels(""): got 0, want 2', False),
                                ('count_vowels("queue"): got 4, want 7', False)):
            with self.subTest(failure=failure):
                self.assertEqual(stated, example_checks.is_stated_in(failure, question))
        self.assertTrue(example_checks.is_stated_in("is_even(4): got 0, want 1", QUESTION))
        self.assertTrue(example_checks.is_stated_in("f(2): got 3, want 4.", "f(2) returns 4."))
        self.assertFalse(example_checks.is_stated_in("pad('x'): got 'x', want 'xa'", "pad('x') gives 'xab'"))

    def test_every_wanted_value_only_counts_as_stated_next_to_the_same_call(self):
        palindrome = ("Write is_palindrome(s) that returns True if s reads the same backwards and False otherwise. "
                      'is_palindrome("Level") gives True.')
        fizz = 'Write a function fizz(n) that returns "Fizz" for multiples of 3, "Buzz" for multiples of 5. fizz(3) gives "Fizz".'
        primes = "Write is_prime(n) in C. is_prime(7) gives 1 and is_prime(10) gives 0."
        cases = (
            (palindrome, 'is_palindrome("ab a"): got False, want True', False),
            (palindrome, 'is_palindrome("abc"): got True, want False', False),
            (palindrome, 'is_palindrome("Level"): got False, want True', True),
            (fizz, 'fizz(7): got "7", want "Fizz"', False),
            (fizz, 'fizz(10): got "Fizz", want "Buzz"', False),
            (fizz, 'fizz(3): got "3", want "Fizz"', True),
            (primes, "is_prime(1): got 1, want 0", False),
            (primes, "is_prime(10): got 1, want 0", True),
            ("Write a bash function add that prints the sum. add 2 3 prints 5.", "add 2 3: got 6, want 5", True),
            ("Write a bash function add that prints the sum. add 2 3 prints 5.", "add 2 30: got 6, want 5", False),
            ("Write a C++ Stack class. After two pushes s.size() returns 2.", "s.size(): got 1, want 2", True),
            ("Write add(a, b). add(2, 3) gives 5.", "add(2, 3) == 5: got False, want True", True),
            ("Write add(a, b). add(2, 3) gives 5.", "add(2, 4) == 5: got False, want True", False),
            ("Write price(n). price(3) returns 1.5.", "price(3): got 1.4, want 1.50", True),
            ("Write price(n). price(3) returns 1.5.", "price(3): got 1.4, want 1.55", False),
        )
        for question, failure, stated in cases:
            with self.subTest(failure=failure):
                self.assertEqual(stated, example_checks.is_stated_in(failure, question))

    def test_a_value_belongs_to_the_nearest_call_and_may_come_before_it(self):
        cases = (
            ('is_palindrome("Level") gives True and is_palindrome("abc") gives False.', 'is_palindrome("Level"): got True, want False',
             False),
            ('is_palindrome("Level") gives True and is_palindrome("abc") gives False.', 'is_palindrome("abc"): got True, want False',
             True),
            ("add(1, 2) gives 3 and add(2, 2) gives 4", "add(1, 2): got 3, want 4", False),
            ("add(1, 2) gives 3 and add(2, 2) gives 4", "add(2, 2): got 4, want 3", False),
            ("add(1, 2) gives 3 and add(2, 2) gives 4", "add(2, 2): got 5, want 4", True),
            ("fib(1) gives 1, and fib(10) gives 55", "fib(1): got 1, want 10", False),
            ("double(2) is 4 and double(8) is 16", "double(2): got 4, want 8", False),
            ("It should return 5 for add(2, 3).", "add(2, 3): got 6, want 5", True),
            ("Expected: True for is_even(4), False for is_even(3).", "is_even(3): got True, want False", True),
            ("Expected: True for is_even(4), False for is_even(3).", "is_even(4): got False, want True", True),
            ("Expected: True for is_even(4), False for is_even(3).", "is_even(4): got True, want False", False),
            ('Solution.isBalanced("()") should be true', 'isBalanced("()"): got false, want true', True),
            ('isBalanced("()") should be true', 'Solution.isBalanced("()"): got false, want true', True),
            ("area(width=2, height=3) should be 6", "area(2, 3): got 5, want 6", True),
            ('count_vowels("The quick brown fox jumps over the lazy dog near the old river bank") should return 20',
             'count_vowels("The quick brown fox jumps over the lazy dog near the old river bank"): got 19, want 20', True),
            ("total(1000, 500) is 1,500", "total(1000, 500): got 1000, want 1500", True),
            ("f(1,500) is 3", "f(1,500): got 2, want 1500", False),
        )
        for question, failure, stated in cases:
            with self.subTest(question=question, failure=failure):
                self.assertEqual(stated, example_checks.is_stated_in(failure, question))

    def test_tuples_brackets_in_strings_and_a_call_with_its_own_value(self):
        cases = (
            ("min_max([3, 1, 2]) should return (1, 3)", "min_max([3, 1, 2]): got (1, 2), want (1, 3)", True),
            ("divide(7, 2) returns (3, 1)", "divide(7, 2): got (3, 0), want (3, 1)", True),
            ("split_name('Ada Lovelace') gives ('Ada', 'Lovelace')",
             "split_name('Ada Lovelace'): got ('Ada',), want ('Ada', 'Lovelace')", True),
            ('is_balanced("(()") should return False', 'is_balanced("(()"): got True, want False', True),
            ('opening("(") returns 1', 'opening("("): got 0, want 1', True),
            ('is_balanced("((") is False and is_balanced("()") is True', 'is_balanced("()"): got False, want True', True),
            ('count_open("a(b") is 1. add(2, 3) is 5.', "add(2, 3): got 6, want 5", True),
            ("It should be True for even numbers: is_even(3) is False", "is_even(3): got False, want True", False),
            ('Return True for palindromes: is_palindrome("abc") is False', 'is_palindrome("abc"): got False, want True', False),
            ("Here are 3 examples: add(1, 1) is 2", "add(1, 1): got 2, want 3", False),
            ("It should be True for is_even(4).", "is_even(4): got False, want True", True),
            ("It's said (in the notes) that f(2) gives 4", "f(2): got 3, want 4", True),
            ("add(2, 3) returns 5\nUse 4 spaces to indent", "add(2, 3): got 5, want 4", False),
            ("add(2, 3) returns 5 in under 10 ms", "add(2, 3): got 5, want 10", False),
            ("add(2, 3) gives the sum 5", "add(2, 3): got 6, want 5", True),
        )
        for question, failure, stated in cases:
            with self.subTest(question=question, failure=failure):
                self.assertEqual(stated, example_checks.is_stated_in(failure, question))

    def test_a_memory_error_while_running_c_checks_counts(self):
        outcome = example_checks.outcome_of(CRASHED, "c", QUESTION)
        self.assertIn("heap-buffer-overflow", outcome.crash)

    def test_an_exception_in_script_checks_is_not_blamed_on_the_code(self):
        raised = {**CRASHED, "problem": "Traceback (most recent call last):\nValueError: Input cannot be empty."}
        self.assertIsNone(example_checks.outcome_of(raised, "python", QUESTION))
        self.assertIsNone(example_checks.outcome_of({**CRASHED, "problem": "AssertionError"}, "c", QUESTION))

    def test_checks_that_do_not_build_or_print_nothing_are_ignored(self):
        self.assertIsNone(example_checks.outcome_of(CHECKS_BROKEN, "c", QUESTION))
        self.assertIsNone(example_checks.outcome_of(PASSED, "c", QUESTION))
        self.assertIsNone(example_checks.outcome_of(None, "c", QUESTION))

    def test_checks_for_a_question_without_examples_are_not_said_to_come_from_them(self):
        outcome = example_checks.Outcome(passed=2)
        self.assertEqual("the 2 checks I wrote for it pass",
                         example_checks.summary_part(outcome, "Write a C function that frees a linked list."))
        self.assertEqual("the 2 checks I wrote from the question's examples pass", example_checks.summary_part(outcome, QUESTION))
        crashed = example_checks.Outcome(crash="==1==ERROR: AddressSanitizer: heap-buffer-overflow")
        self.assertIn("a check I wrote for it:", example_checks.summary_part(crashed, "Write a C function that frees a list."))

    def test_failures_on_results_the_question_does_not_state_are_not_reported(self):
        outcome = example_checks.Outcome(passed=2, unsure=1)
        self.assertEqual("", example_checks.summary_part(outcome))


class RewritesFromTheQuestionsExamples(unittest.TestCase):
    def test_code_that_fails_a_check_is_sent_back_with_the_check_and_the_fix_is_given(self):
        outcome, engine, checked = run_turn([said(WRONG), said(CHECKS), said(RIGHT)],
                                            [PASSED, CHECKS_FAIL, PASSED, CHECKS_PASS])
        self.assertEqual(3, len(engine.chat_bodies))
        writer_prompt = last_user_note(engine.chat_bodies[1])
        self.assertIn(QUESTION, writer_prompt)
        self.assertIn("return n % 2;", writer_prompt)
        note = last_user_note(engine.chat_bodies[2])
        self.assertIn("CHECK FAIL: is_even(4): got 0, want 1", note)
        self.assertIn("is_even(4) == 1", note)
        self.assertIn("int shown_main(", checked[1].code)
        self.assertIn("return n % 2 == 0;", checked[3].code)
        self.assertTrue(outcome["answer"].startswith(RIGHT))
        self.assertTrue(outcome["answer"].endswith(
            "Sandbox check: the C ran without errors (gcc); the 2 checks I wrote from the question's examples pass."))

    def test_check_runs_whose_main_is_replaced_ask_the_runner_not_to_excuse_input(self):
        _, _, checked = run_turn([said(WRONG), said(CHECKS)], [PASSED, CHECKS_PASS])
        self.assertEqual([True, False], [unit.input_excuse for unit in checked])

    def test_a_crash_on_an_edge_case_is_sent_back(self):
        outcome, engine, _ = run_turn([said(WRONG), said(CHECKS), said(RIGHT)], [PASSED, CRASHED, PASSED, CHECKS_PASS])
        self.assertIn("heap-buffer-overflow", last_user_note(engine.chat_bodies[2]))
        self.assertTrue(outcome["answer"].startswith(RIGHT))

    def test_checks_still_failing_after_the_last_rewrite_are_admitted(self):
        outcome, engine, _ = run_turn([said(WRONG), said(CHECKS), said(WRONG), said(WRONG)],
                                      [PASSED, CHECKS_FAIL, PASSED, CHECKS_FAIL, PASSED, CHECKS_FAIL])
        self.assertEqual(4, len(engine.chat_bodies))
        self.assertTrue(outcome["answer"].endswith(
            "1 of the 2 checks I wrote from the question's examples fail (is_even(4): got 0, want 1)."))

    def test_code_with_nothing_to_check_is_answered_as_it_was(self):
        outcome, engine, checked = run_turn([said(RIGHT), said("NONE")], [PASSED])
        self.assertEqual(2, len(engine.chat_bodies))
        self.assertEqual(1, len(checked))
        self.assertTrue(outcome["answer"].endswith("Sandbox check: the C ran without errors (gcc)."))

    def test_checks_that_do_not_build_change_nothing(self):
        outcome, engine, _ = run_turn([said(RIGHT), said(CHECKS)], [PASSED, CHECKS_BROKEN])
        self.assertEqual(2, len(engine.chat_bodies))
        self.assertTrue(outcome["answer"].endswith("Sandbox check: the C ran without errors (gcc)."))

    def test_the_rewrite_after_a_failed_check_thinks_when_the_model_can(self):
        _, engine, _ = run_turn([said(WRONG), said(CHECKS), said(RIGHT)], [PASSED, CHECKS_FAIL, PASSED, CHECKS_PASS],
                                config={"model": "m", "think": "auto", "example_checks": ["c"]}, model=THINKING_MODEL)
        self.assertFalse(engine.chat_bodies[0]["think"])
        self.assertFalse(engine.chat_bodies[1]["think"])
        self.assertTrue(engine.chat_bodies[2]["think"])

    def test_a_question_without_stated_examples_gets_no_checks_unless_it_is_c(self):
        python = code_sandbox.Unit("python", "def f():\n    return 1\n", "", "Python")
        c_code = code_sandbox.Unit("c", "int f(void) { return 1; }\n", "", "C")
        plain = "Write a function that merges overlapping intervals and returns them sorted."
        self.assertFalse(example_checks.is_worth_checking(plain, python))
        self.assertTrue(example_checks.is_worth_checking(plain, c_code))
        self.assertTrue(example_checks.is_worth_checking("so join_by , a b c prints a,b,c", python))
        self.assertTrue(example_checks.is_worth_checking("strings like '1h30m' or '45s'", python))

    def test_only_the_languages_the_setting_names_get_checks(self):
        outcome, engine, checked = run_turn([said(WRONG)], [PASSED], config={"model": "m", "example_checks": ["rust"]})
        self.assertEqual(1, len(engine.chat_bodies))
        self.assertEqual(1, len(checked))
        self.assertTrue(outcome["answer"].endswith("Sandbox check: the C ran without errors (gcc)."))

    def test_without_the_setting_no_checks_are_written(self):
        outcome, engine, checked = run_turn([said(WRONG)], [PASSED], config={"model": "m"})
        self.assertEqual(1, len(engine.chat_bodies))
        self.assertEqual(1, len(checked))


class ThinksBeforeFixingFailingCode(unittest.TestCase):
    def test_the_rewrite_after_a_sandbox_error_thinks_when_the_model_can(self):
        failed = {"status": "failed", "step": "compile", "checked": [], "notes": [], "tool": "gcc", "seconds": 0.2,
                  "problem": "main.c:4:12: error: expected ';' before '}' token"}
        _, engine, _ = run_turn([said(WRONG), said(RIGHT)], [failed, PASSED],
                                config={"model": "m", "think": "auto"}, model=THINKING_MODEL)
        self.assertEqual([False, True], [body["think"] for body in engine.chat_bodies])


if __name__ == "__main__":
    unittest.main()

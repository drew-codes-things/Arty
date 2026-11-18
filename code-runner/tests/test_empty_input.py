import base64
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import languages  # noqa: E402
import runner  # noqa: E402

SUCCESS = runner.StepResult("success", 0, "", 0.1)
RUNNER_C_FLAGS = ("-std=gnu17", "-fsanitize=address,undefined", "-fno-sanitize-recover=all")
RUNNER_CPP_FLAGS = ("-std=gnu++17", "-fsanitize=address,undefined", "-fno-sanitize-recover=all")
NEVER_PASSED = frozenset({runner.NEEDS_INPUT, "failed"})
NEVER_FAILED = frozenset({runner.NEEDS_INPUT, "passed"})
PYTHON_EMPTY_INPUT = {
    "two numbers from one stdin line": "import sys\na, b = map(int, sys.stdin.readline().split())\nprint(a + b)\n",
    "first token of all stdin": "import sys\ndata = sys.stdin.read().split()\nn = int(data[0])\nprint(n * 2)\n",
    "json from stdin": "import json, sys\nrecords = json.load(sys.stdin)\nprint(len(records))\n",
    "average of stdin numbers": "import sys\nnumbers = [float(line) for line in sys.stdin]\nprint(sum(numbers) / len(numbers))\n",
    "argv unpacked": "import sys\n_, source, target = sys.argv\nprint(source, target)\n",
    "argv checked, raises": "import sys\nif len(sys.argv) < 2:\n    raise ValueError('give me a file name')\nprint(sys.argv[1])\n",
    "input() at end of input": "name = input('Name: ')\nprint(name.upper())\n",
    "argv handed to main(argv)": "import sys\n\ndef main(argv):\n    name = argv[1]\n    print(name)\n\nmain(sys.argv)\n",
    "stdin read by a helper": ("import sys\n\ndef read_numbers():\n    return [int(x) for x in sys.stdin.read().split()]\n\n"
                               "numbers = read_numbers()\nprint(max(numbers))\n"),
    "assert on argv": "import sys\nassert len(sys.argv) == 2, 'usage: main.py FILE'\nprint(sys.argv[1])\n",
    "a key of json from stdin": "import json, sys\nrecord = json.loads(sys.stdin.read() or '{}')\nprint(record['name'])\n",
    "a regex match on a stdin line": "import re, sys\nfound = re.match(r'(\\d+)', sys.stdin.readline())\nprint(found.group(1))\n",
}
PYTHON_NEWLY_FAILED = {
    "a parse helper given a stdin line": "import sys\n\ndef parse(text):\n    return int(text)\n\nprint(parse(sys.stdin.readline()))\n",
    "a convert helper in a comprehension": ("import sys\n\ndef to_number(text):\n    return float(text)\n\n"
                                            "numbers = [to_number(line) for line in sys.stdin.read().split('\\n')]\nprint(sum(numbers))\n"),
    "EOFError turned into a clearer error": ("try:\n    number = int(input())\nexcept EOFError:\n"
                                             "    raise ValueError('no number given')\nprint(number)\n"),
    "a missing file turned into a clearer error": ("try:\n    handle = open('config.json')\nexcept FileNotFoundError as error:\n"
                                                   "    raise RuntimeError('config.json is missing') from error\nprint(handle.read())\n"),
}
PYTHON_CRASHES_BESIDE_INPUT = {
    "argv has a default, off-by-one after it": ("import sys\nn = int(sys.argv[1]) if len(sys.argv) > 1 else 10\n"
                                                "squares = [i * i for i in range(n)]\nprint(squares[n])\n"),
    "EOFError is caught, off-by-one after it": ("try:\n    n = int(input())\nexcept EOFError:\n    n = 5\n"
                                                "numbers = list(range(n))\nprint(numbers[n])\n"),
    "stdin has a default, off-by-one after it": ("import sys\ntext = sys.stdin.read() or 'the quick brown fox'\n"
                                                 "words = text.split()\nprint(words[len(words)])\n"),
    "a class that keeps one argv value in self": ("import sys\n\nclass Stats:\n    def __init__(self):\n        self.values = [1, 2, 3]\n"
                                                  "        self.name = sys.argv[1] if len(sys.argv) > 1 else 'demo'\n\n"
                                                  "    def last(self):\n        return self.values[len(self.values)]\n\n"
                                                  "print(Stats().last())\n"),
    "a self-test with an empty list": ("import sys\n\ndef average(values):\n    return sum(values) / len(values)\n\n"
                                       "print(average([]))\nif len(sys.argv) > 1:\n    values = [float(v) for v in sys.argv[1:]]\n"
                                       "    print(average(values))\n"),
    "a bug under an if that names a flag": ("import sys\nverbose = '-v' in sys.argv\nitems = [1, 2, 3]\nif not verbose:\n"
                                            "    print(items[3])\n"),
    "division by a literal zero beside an argv default": ("import sys\nscale = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0\n"
                                                          "print(scale / 0)\n"),
}
PYTHON_REAL_BUGS = {
    "an index error in a program that reads nothing": "items = [1]\nprint(items[3])\n",
    "a division error in a program that reads nothing": "count = 0\nprint(10 / count)\n",
    "input named only in a comment and a string": "# no sys.argv here\nitems = [1]\nprint('input()')\nprint(items[3])\n",
}
C_EMPTY_INPUT = {
    "divides two numbers it read": "#include <stdio.h>\nint main(void) {\n    int a, b;\n    scanf(\"%d %d\", &a, &b);\n"
                                   "    printf(\"%d\\n\", a / b);\n    return 0;\n}\n",
    "scanf in a helper, division in main": ("#include <stdio.h>\nint read_int(void) {\n    int value = 0;\n    scanf(\"%d\", &value);\n"
                                            "    return value;\n}\nint main(void) {\n    int count = read_int();\n"
                                            "    printf(\"%d\\n\", 100 / count);\n    return 0;\n}\n"),
    "fgets and atoi, then a division": ("#include <stdio.h>\n#include <stdlib.h>\nint main(void) {\n    char line[32] = \"\";\n"
                                        "    fgets(line, sizeof line, stdin);\n    int count = atoi(line);\n"
                                        "    printf(\"%d\\n\", 100 / count);\n    return 0;\n}\n"),
    "scanf and the division on one line": ("#include <stdio.h>\nint main(void) {\n"
                                           "    int count = 0; scanf(\"%d\", &count); printf(\"%d\\n\", 100 / count);\n    return 0;\n}\n"),
}
C_CRASHES_BESIDE_INPUT = {
    "overflow on a line whose string holds a newline escape": ("#include <stdio.h>\n#include <limits.h>\nint main(void) {\n    int n = 3;\n"
                                                               "    scanf(\"%d\", &n);\n    int big = INT_MAX;\n"
                                                               "    printf(\"%d\\n\", big + 1);\n    return 0;\n}\n"),
    "overflow on a line that also prints the number read": ("#include <stdio.h>\n#include <limits.h>\nint main(void) {\n    int count = 3;\n"
                                                            "    scanf(\"%d\", &count);\n    int big = INT_MAX;\n"
                                                            "    printf(\"%d %d\", count, big + 1);\n    return 0;\n}\n"),
    "a shift too far on a line with a newline escape": ("#include <stdio.h>\nint main(void) {\n    int n = 3, bits = 40;\n"
                                                        "    scanf(\"%d\", &n);\n    printf(\"%d\\n\", 1 << bits);\n    return 0;\n}\n"),
}
C_REAL_BUGS = {
    "a commented-out scanf": ("#include <stdio.h>\nint main(void) {\n    int total = 10, count = 0;\n"
                              "    // scanf(\"%d\", &count);\n    printf(\"%d\", total / count);\n    return 0;\n}\n"),
    "overflow in a program that reads nothing": ("#include <stdio.h>\n#include <limits.h>\nint main(void) {\n    int big = INT_MAX;\n"
                                                 "    printf(\"%d\\n\", big + 1);\n    return 0;\n}\n"),
}
CPP_EMPTY_INPUT = "#include <iostream>\nint main() {\n    int count = 0;\n    std::cin >> count;\n    std::cout << 100 / count << std::endl;\n}\n"
JAVASCRIPT_PROMPT = "const name = prompt('What is your name?');\nconsole.log('Hello ' + name);\n"
BASH_NEWLY_FAILED = {
    "last line read from stdin, with set -u": "set -u\nwhile read -r line; do\n  last=$line\ndone\necho \"$last\"\n",
    "a token from the environment, with set -u": "set -euo pipefail\necho \"Authorization: Bearer $SERVICE_TOKEN\"\n",
}


def judged(language: str, code: str, output: str, exit_code: int = 1, result: str = "exit-code",
           given: runner.RunInput = runner.NO_INPUT, run_with=None) -> dict:
    plan = languages.plan_for(language, code)
    results = [SUCCESS] * (len(plan.steps) - 1) + [runner.StepResult(result, exit_code, output, 0.1)]
    step, reruns = plan.steps[-1], ()
    if run_with and runner.wants_a_neutral_rerun(language, code, step, results[-1], True, given):
        reruns = runner.neutral_reruns(language, code, step, results[-1], run_with)
    return runner.judged(language, code, plan, results, given=given, reruns=reruns)


class RealProgramsWithEmptyInput(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def run_program(self, command: list[str], name: str, code: str, stdin: str | None = None) -> tuple[int, str]:
        (self.folder / name).write_text(code)
        feeding = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
        ran = subprocess.run(command, cwd=self.folder, capture_output=True, text=True, timeout=30, **feeding)
        return ran.returncode, ran.stdout + ran.stderr

    def neutral_rerun(self, command: list[str]):
        return lambda neutral: self.ran_with(command, neutral)

    def ran_with(self, command: list[str], neutral: runner.RunInput) -> runner.StepResult:
        ran = subprocess.run(command + list(neutral.arguments), cwd=self.folder, input=neutral.stdin, capture_output=True,
                             text=True, timeout=30)
        result = "success" if ran.returncode == 0 else ("signal" if ran.returncode < 0 else "exit-code")
        return runner.StepResult(result, ran.returncode, ran.stdout + ran.stderr, 0.1, ran.stdout)

    def python_rerun(self):
        return self.neutral_rerun([sys.executable, "-I", "main.py"])

    def run_python(self, code: str, stdin: str | None = None) -> tuple[int, str]:
        return self.run_program([sys.executable, "-I", "main.py"], "main.py", code, stdin)

    def run_c(self, code: str, compiler: str = "gcc", flags: tuple = RUNNER_C_FLAGS, name: str = "main.c") -> tuple[int, str, str]:
        (self.folder / name).write_text(code)
        subprocess.run([compiler, *flags, "-o", "main", name], cwd=self.folder, check=True, capture_output=True, timeout=120)
        ran = subprocess.run(["./main"], cwd=self.folder, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        return ran.returncode, ran.stdout + ran.stderr, "exit-code" if ran.returncode >= 0 else "signal"

    def assert_status_among(self, wanted: frozenset, verdict: dict, output: str):
        self.assertIn(verdict["status"], wanted, output)

    def test_python_that_dies_of_empty_input_needs_input(self):
        for label, code in PYTHON_EMPTY_INPUT.items():
            with self.subTest(case=label):
                exit_code, output = self.run_python(code)
                self.assertNotEqual(0, exit_code, output)
                verdict = judged("python", code, output, exit_code, run_with=self.python_rerun())
                self.assertEqual(runner.NEEDS_INPUT, verdict["status"], output)
                self.assertIn(runner.READS_INPUT_NOTE, verdict["notes"][-1])

    def test_the_python_programs_that_newly_failed_never_fail(self):
        for label, code in PYTHON_NEWLY_FAILED.items():
            with self.subTest(case=label):
                exit_code, output = self.run_python(code)
                self.assert_status_among(NEVER_FAILED, judged("python", code, output, exit_code, run_with=self.python_rerun()), output)

    def test_python_crashes_beside_input_never_pass(self):
        for label, code in PYTHON_CRASHES_BESIDE_INPUT.items():
            with self.subTest(case=label):
                exit_code, output = self.run_python(code)
                self.assertNotEqual(0, exit_code, output)
                self.assert_status_among(NEVER_PASSED, judged("python", code, output, exit_code, run_with=self.python_rerun()), output)

    def test_python_crashes_in_programs_that_read_nothing_fail(self):
        for label, code in PYTHON_REAL_BUGS.items():
            with self.subTest(case=label):
                exit_code, output = self.run_python(code)
                verdict = judged("python", code, output, exit_code)
                self.assertEqual(("failed", "run"), (verdict["status"], verdict.get("step")), output)

    def test_a_crash_with_the_input_given_fails(self):
        code = PYTHON_CRASHES_BESIDE_INPUT["EOFError is caught, off-by-one after it"]
        exit_code, output = self.run_python(code, "3\n")
        verdict = judged("python", code, output, exit_code, given=runner.RunInput(stdin="3\n"))
        self.assertEqual(("failed", "run"), (verdict["status"], verdict.get("step")), output)

    def test_a_program_given_its_input_runs_and_passes(self):
        code = PYTHON_EMPTY_INPUT["two numbers from one stdin line"]
        exit_code, output = self.run_python(code, "2 3\n")
        self.assertEqual((0, "5"), (exit_code, output.strip()))

    @unittest.skipUnless(shutil.which("gcc"), "needs gcc")
    def test_c_undefined_behaviour_with_a_scanf_needs_input_only_when_input_avoids_it(self):
        cases = [(code, runner.NEEDS_INPUT) for code in C_EMPTY_INPUT.values()]
        cases += [(code, "failed") for code in C_CRASHES_BESIDE_INPUT.values()]
        for code, wanted in cases:
            with self.subTest(code=code):
                exit_code, output, result = self.run_c(code)
                self.assertIn("runtime error", output)
                verdict = judged("c", code, output, exit_code, result, run_with=self.neutral_rerun(["./main"]))
                self.assertEqual(wanted, verdict["status"], output)

    @unittest.skipUnless(shutil.which("gcc"), "needs gcc")
    def test_c_undefined_behaviour_in_a_program_that_reads_nothing_fails(self):
        for label, code in C_REAL_BUGS.items():
            with self.subTest(case=label):
                exit_code, output, result = self.run_c(code)
                verdict = judged("c", code, output, exit_code, result)
                self.assertEqual(("failed", "run"), (verdict["status"], verdict.get("step")), output)

    @unittest.skipUnless(shutil.which("g++"), "needs g++")
    def test_cpp_that_divides_by_a_count_it_never_got_needs_input(self):
        exit_code, output, result = self.run_c(CPP_EMPTY_INPUT, "g++", RUNNER_CPP_FLAGS, "main.cpp")
        rerun = self.neutral_rerun(["./main"])
        self.assertEqual(runner.NEEDS_INPUT, judged("cpp", CPP_EMPTY_INPUT, output, exit_code or 1, result, run_with=rerun)["status"],
                         output)

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_javascript_that_asks_with_prompt_never_fails(self):
        exit_code, output = self.run_program(["node", "main.js"], "main.js", JAVASCRIPT_PROMPT)
        self.assert_status_among(NEVER_FAILED, judged("javascript", JAVASCRIPT_PROMPT, output, exit_code), output)

    def test_bash_that_newly_failed_never_fails(self):
        for label, code in BASH_NEWLY_FAILED.items():
            with self.subTest(case=label):
                exit_code, output = self.run_program(["bash", "main.sh"], "main.sh", code)
                rerun = self.neutral_rerun(["bash", "main.sh"])
                self.assert_status_among(NEVER_FAILED, judged("bash", code, output, exit_code, run_with=rerun), output)


class InputGivenToTheSandbox(unittest.TestCase):
    def test_stdin_and_arguments_reach_only_the_run_step(self):
        plan = languages.plan_for("python", "import sys\nprint(sys.argv[1], input())\n")
        given = runner.RunInput(stdin="5\n", arguments=("--size", "3"))
        lint, run = plan.steps[-2], plan.steps[-1]
        run_command = runner.sandbox_command("u", Path("/srv/runs/abc"), run, plan, None, given)
        lint_command = runner.sandbox_command("u", Path("/srv/runs/abc"), lint, plan, None, given)
        self.assertIn("StandardInputData=" + base64.b64encode(b"5\n").decode(), run_command)
        self.assertEqual(["--size", "3"], run_command[-2:])
        self.assertIn(runner.NO_STANDARD_INPUT, lint_command)
        self.assertNotIn("--size", lint_command)

    def test_without_input_the_run_step_reads_nothing(self):
        plan = languages.plan_for("python", "print(1)\n")
        command = runner.sandbox_command("u", Path("/srv/runs/abc"), plan.steps[-1], plan, None)
        self.assertIn(runner.NO_STANDARD_INPUT, command)

    def test_input_fields_are_checked(self):
        self.assertEqual(runner.RunInput("1 2\n", ("a",)), runner.given_input({"stdin": "1 2\n", "args": ["a"]}))
        self.assertFalse(runner.given_input({}))
        for payload in ({"stdin": 5}, {"stdin": "x" * (runner.MAX_INPUT_CHARS + 1)}, {"args": "a b"}, {"args": [1]},
                        {"args": ["a"] * (runner.MAX_ARGUMENTS + 1)}, {"stdin": "a\x00b"}):
            with self.subTest(payload=str(payload)[:40]), self.assertRaises(runner.BadRequest):
                runner.given_input(payload)


if __name__ == "__main__":
    unittest.main()

import json
import sys
import threading
import time
import unittest
import unittest.mock
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import languages  # noqa: E402
import runner  # noqa: E402

SUCCESS = runner.StepResult("success", 0, "", 0.1)


def failure(output: str, result: str = "exit-code", exit_code: int = 1) -> runner.StepResult:
    return runner.StepResult(result, exit_code, output, 0.1)


class PlanTest(unittest.TestCase):
    def test_python_runs_after_syntax_and_lint(self):
        plan = languages.plan_for("python", "print(1)\n")
        self.assertEqual(["syntax", "lint", "run"], [step.name for step in plan.steps])
        self.assertEqual("/usr/bin/python3", plan.steps[0].command[0])

    def test_python_314_is_used_when_asked(self):
        plan = languages.plan_for("python", "print(1)\n", "3.14")
        self.assertEqual("/usr/local/bin/python3.14", plan.steps[0].command[0])
        self.assertEqual("Python 3.14", plan.tool)

    def test_python_with_a_missing_package_is_not_run(self):
        plan = languages.plan_for("python", "import numpy as np\nfrom flask import Flask\nimport os\n")
        self.assertNotIn("run", [step.name for step in plan.steps])
        self.assertIn("numpy and flask", plan.notes[0])

    def test_python_test_functions_are_run_as_tests(self):
        plan = languages.plan_for("python", "def test_add():\n    assert 1 + 1 == 2\n")
        self.assertEqual("tests", plan.steps[-1].name)

    def test_browser_javascript_is_only_syntax_checked(self):
        plan = languages.plan_for("javascript", "document.querySelector('#a').textContent = 'x';\n")
        self.assertEqual(["syntax"], [step.name for step in plan.steps])
        self.assertIn("browser", plan.notes[0])

    def test_jsx_is_left_unchecked(self):
        self.assertTrue(languages.plan_for("javascript", "const App = () => <div>Hi</div>;\n").unchecked)

    def test_typescript_generics_are_not_mistaken_for_jsx(self):
        plan = languages.plan_for("typescript", "const names: Array<String> = [];\nconst m = new Map<string, number>();\n")
        self.assertFalse(plan.unchecked)

    def test_javascript_packages_are_found_but_builtins_are_not(self):
        code = "const fs = require('fs');\nimport express from 'express';\nimport { x } from '@scope/pkg/sub';\nimport p from 'node:path';\n"
        self.assertEqual(["express", "@scope/pkg"], languages.javascript_packages(code))

    def test_rust_crates_are_found_but_std_paths_and_turbofish_are_not(self):
        self.assertEqual([], languages.rust_crates("use std::{fmt, io};\nfn main() { io::stdin(); let x = v.iter().sum::<i32>(); }"))
        self.assertEqual(["serde"], languages.rust_crates("use serde::Serialize;\nfn main() {}"))
        self.assertTrue(languages.plan_for("rust", "use tokio::time;\nfn main() {}").unchecked)

    def test_rust_without_main_is_compiled_as_a_library(self):
        plan = languages.plan_for("rust", "pub fn add(a: i32, b: i32) -> i32 { a + b }\n")
        self.assertIn("lib", plan.steps[0].command)
        self.assertEqual(1, len(plan.steps))

    def test_rust_tests_are_run(self):
        plan = languages.plan_for("rust", "#[test]\nfn adds() { assert_eq!(2, 1 + 1); }\n")
        self.assertEqual(["compile", "tests"], [step.name for step in plan.steps])

    def test_go_without_a_package_clause_gets_one(self):
        plan = languages.plan_for("go", "import \"fmt\"\n\nfunc main() { fmt.Println(1) }\n")
        self.assertTrue(plan.files["main.go"].startswith("package main"))
        self.assertTrue(plan.go_cache)

    def test_go_with_outside_modules_is_only_syntax_checked(self):
        plan = languages.plan_for("go", "package main\n\nimport (\n\t\"fmt\"\n\t\"github.com/gin-gonic/gin\"\n)\n\nfunc main() {}\n")
        self.assertEqual(["syntax"], [step.name for step in plan.steps])
        self.assertIn("github.com/gin-gonic/gin", plan.notes[0])

    def test_java_file_is_named_after_the_public_class(self):
        plan = languages.plan_for("java", "public class Greeter {\n  public static void main(String[] a) {}\n}\n")
        self.assertIn("Greeter.java", plan.files)
        self.assertEqual("Greeter", plan.steps[-1].command[-1])

    def test_java_with_outside_libraries_is_unchecked(self):
        self.assertTrue(languages.plan_for("java", "import org.junit.Test;\nclass A {}\n").unchecked)

    def test_csharp_top_level_statements_run_but_class_libraries_do_not(self):
        self.assertTrue(languages.csharp_has_entry_point("Console.WriteLine(1);\n"))
        self.assertTrue(languages.csharp_has_entry_point("class P {\n    static void Main() {}\n}\n"))
        self.assertFalse(languages.csharp_has_entry_point("using System;\npublic class Point {\n    public int X;\n}\n"))

    def test_csharp_needs_its_compiler(self):
        self.assertIn("isn't installed", languages.plan_for("csharp", "Console.WriteLine(1);\n").unchecked)
        plan = languages.plan_for("csharp", "Console.WriteLine(1);\n", compiler="/x/csc.dll", references=("/x/a.dll",))
        self.assertIn("-r:/x/a.dll", plan.steps[0].command)

    def test_sql_for_other_databases_is_unchecked(self):
        self.assertTrue(languages.plan_for("sql", "SELECT 1;", "postgresql").unchecked)
        self.assertTrue(languages.plan_for("sql", "CREATE TABLE t (id SERIAL PRIMARY KEY);").unchecked)
        self.assertFalse(languages.plan_for("sql", "SELECT 1;").unchecked)

    def test_data_with_placeholders_is_unchecked(self):
        self.assertTrue(languages.plan_for("json", '{"a": 1, ...}').unchecked)
        self.assertFalse(languages.plan_for("xml", "<root><item/></root>").unchecked)

    def test_input_and_arguments_are_recognised(self):
        for language, code in (("python", "name = input('Name: ')"), ("python", "import sys\nprint(sys.argv[1])"),
                               ("java", "Scanner s = new Scanner(System.in);"),
                               ("rust", "let args: Vec<String> = std::env::args().collect();"), ("bash", "read -r line"),
                               ("bash", "echo \"$1\""), ("powershell", "$name = Read-Host 'Name'")):
            with self.subTest(code=code):
                self.assertTrue(languages.reads_input_or_arguments(language, code))
        self.assertFalse(languages.reads_input_or_arguments("python", "print('hello')"))

    def test_reading_odd_long_code_for_input_stays_fast(self):
        longest = runner.MAX_CODE_CHARS
        shapes = ('"\\', "'\\", "/*", '"""', "'''", "`", "--", "#")
        for language in (*languages.NOT_CODE, "c"):
            for shape in shapes:
                code = shape * (longest // len(shape))
                with self.subTest(language=language, shape=shape):
                    started = time.monotonic()
                    languages.reads_input_or_arguments(language, code)
                    self.assertLess(time.monotonic() - started, 1.0)

    def test_input_markers_in_comments_and_strings_do_not_count(self):
        for language, code in (("python", "# reads sys.argv later\nprint(1)"), ("python", "print('use input() here')"),
                               ("python", '"""argparse would be overkill"""\nprint(1)'),
                               ("c", "// scanf( is unsafe\nint main(void) { return 0; }"),
                               ("c", "/* fgets( here */ int main(void) { puts(\"scanf(\"); return 0; }"),
                               ("rust", "fn f<'a>(x: &'a str) {}\n// std::env::args\nfn main() {}"),
                               ("bash", "# read -r line\necho 'uses $1 later'")):
            with self.subTest(code=code):
                self.assertFalse(languages.reads_input_or_arguments(language, code))
        for language, code in (("bash", "echo \"$1\""), ("bash", "echo ${#1} $1"), ("rust", "fn f<'a>(x: &'a str) { let a = std::env::args(); }"),
                               ("python", "x = '#'\nname = input()")):
            with self.subTest(code=code):
                self.assertTrue(languages.reads_input_or_arguments(language, code))

    def test_an_apostrophe_inside_double_quotes_does_not_hide_the_rest_of_a_shell_line(self):
        for language, code in (("bash", "echo \"Don't forget\"; read -r answer"), ("bash", "echo \"Can't open $1\""),
                               ("powershell", "Write-Host \"Don't stop\"; $name = Read-Host"),
                               ("bash", "echo \"it's # not a comment\" $1")):
            with self.subTest(code=code):
                self.assertTrue(languages.reads_input_or_arguments(language, code))
        for language, code in (("bash", "echo \"Don't\" 'read x'"), ("bash", "echo \"Don't\" # read x")):
            with self.subTest(code=code):
                self.assertFalse(languages.reads_input_or_arguments(language, code))

    def test_shell_argument_markers_only_count_in_shell_code(self):
        self.assertFalse(languages.reads_input_or_arguments("javascript", "const out = text.replace(/(a)/, '$1');"))
        self.assertFalse(languages.reads_input_or_arguments("python", "script = 'echo \"$1\"'\nprint(script)"))


class JudgedTest(unittest.TestCase):
    def test_all_steps_succeeding_passes(self):
        plan = languages.plan_for("python", "print(1)\n")
        verdict = runner.judged("python", "print(1)\n", plan, [SUCCESS, SUCCESS, SUCCESS])
        self.assertEqual({"status": "passed", "checked": ["syntax", "lint", "run"], "notes": [], "stdout": ""}, verdict)

    def test_a_traceback_fails_the_run(self):
        plan = languages.plan_for("python", "x = [1]\nprint(x[3])\n")
        output = "Traceback (most recent call last):\n  File \"/work/main.py\", line 2\nIndexError: list index out of range\n"
        verdict = runner.judged("python", "", plan, [SUCCESS, SUCCESS, failure(output)])
        self.assertEqual("failed", verdict["status"])
        self.assertEqual("run", verdict["step"])
        self.assertIn("main.py", verdict["problem"])
        self.assertNotIn("/work/", verdict["problem"])

    def test_lint_noise_is_ignored_but_undefined_names_fail(self):
        plan = languages.plan_for("python", "import os\n")
        unused = failure("main.py:1:1: 'os' imported but unused\n")
        self.assertEqual("passed", runner.judged("python", "", plan, [SUCCESS, unused, SUCCESS])["status"])
        undefined = failure("main.py:3:7: undefined name 'thing'\n")
        verdict = runner.judged("python", "", plan, [SUCCESS, undefined, SUCCESS])
        self.assertEqual(("failed", "lint"), (verdict["status"], verdict["step"]))

    def test_missing_typescript_packages_are_dropped_from_errors(self):
        plan = languages.plan_for("typescript", "import x from 'lodash';\n")
        output = "main.ts(1,15): error TS2307: Cannot find module 'lodash' or its corresponding type declarations.\n"
        verdict = runner.judged("typescript", "", plan, [failure(output, exit_code=2)])
        self.assertEqual("passed", verdict["status"])
        self.assertIn("lodash", verdict["notes"][0])

    def test_files_and_network_trouble_needs_input_and_is_neither_passed_nor_failed(self):
        plan = languages.plan_for("python", "open('data.csv')\n")
        output = "Traceback (most recent call last):\nFileNotFoundError: [Errno 2] No such file or directory: 'data.csv'\n"
        verdict = runner.judged("python", "open('data.csv')\n", plan, [SUCCESS, SUCCESS, failure(output)])
        self.assertEqual((runner.NEEDS_INPUT, ["syntax", "lint"]), (verdict["status"], verdict["checked"]))
        self.assertIn("files, network or input", verdict["notes"][0])

    def test_code_that_reads_input_and_stops_early_needs_input(self):
        code = "import sys\ncount = int(sys.stdin.readline())\nprint(count * 2)\n"
        plan = languages.plan_for("python", code)
        output = ("Traceback (most recent call last):\n  File \"/work/main.py\", line 2, in <module>\n"
                  "    count = int(sys.stdin.readline())\nValueError: invalid literal for int() with base 10: ''\n")
        verdict = runner.judged("python", code, plan, [SUCCESS, SUCCESS, failure(output)])
        self.assertEqual((runner.NEEDS_INPUT, "run"), (verdict["status"], verdict["step"]))
        self.assertIn("input or command-line arguments", verdict["notes"][0])
        self.assertIn("ValueError", verdict["problem"])

    def test_a_crash_that_neutral_input_also_hits_fails_and_one_it_avoids_needs_input(self):
        sanitizer = ("#include <stdio.h>\nint real_main(void) { int n; scanf(\"%d\", &n); return n; }\n"
                     "int main(void) { char b[4]; b[9] = 1; }\n", "==7==ERROR: AddressSanitizer: stack-buffer-overflow\n", "exit-code")
        signal = ("#include <stdio.h>\nint main(void) { char line[8]; fgets(line, 8, stdin); return 0; }\n", "", "core-dump")
        overflow = ("#include <stdio.h>\n#include <limits.h>\nint scale(int n) { return n * 2; }\n"
                    "int main(void) { int n = INT_MAX; printf(\"%d\\n\", scale(n)); scanf(\"%d\", &n); return 0; }\n",
                    "main.c:3:36: runtime error: signed integer overflow: 2147483647 * 2 cannot be represented in type 'int'\n",
                    "exit-code")
        for code, output, result in (sanitizer, signal, overflow):
            with self.subTest(output=output[:40]):
                plan = languages.plan_for("c", code)
                shapes = runner.neutral_inputs("c", code)
                crashed = failure(output.replace("==7==", "==8=="), result, 11)
                reruns = tuple((shape, crashed) for shape in shapes)
                verdict = runner.judged("c", code, plan, [SUCCESS, failure(output, result, 11)], reruns=reruns)
                self.assertEqual(("failed", "run"), (verdict["status"], verdict["step"]))
                verdict = runner.judged("c", code, plan, [SUCCESS, failure(output, result, 11)], reruns=((shapes[0], SUCCESS),))
                self.assertEqual(runner.NEEDS_INPUT, verdict["status"])

    def test_a_crash_of_a_program_that_reads_nothing_fails(self):
        cases = (("c", "#include <stdio.h>\nint main(void) { char b[4]; b[9] = 1; }\n",
                  "==7==ERROR: AddressSanitizer: stack-buffer-overflow on address 0x7ffd\n", "exit-code"),
                 ("c", "int main(void) { int *p = 0; return *p; }\n", "", "core-dump"),
                 ("python", "def greet():\n    return nmae.upper()\n\nprint(greet())\n",
                  "Traceback (most recent call last):\nNameError: name 'nmae' is not defined\n", "exit-code"))
        for language, code, output, result in cases:
            with self.subTest(language=language, output=output[:40]):
                plan = languages.plan_for(language, code)
                results = [SUCCESS] * (len(plan.steps) - 1) + [failure(output, result, 11)]
                verdict = runner.judged(language, code, plan, results)
                self.assertEqual(("failed", "run"), (verdict["status"], verdict["step"]))

    def test_a_crash_with_the_input_given_is_a_failure(self):
        code = "import sys\nitems = [1]\nprint(items[3])\nprint(sys.argv[1])\n"
        output = "Traceback (most recent call last):\nIndexError: list index out of range\n"
        verdict = runner.judged("python", code, languages.plan_for("python", code), [SUCCESS, SUCCESS, failure(output)],
                                given=runner.RunInput(arguments=("a",)))
        self.assertEqual(("failed", "run"), (verdict["status"], verdict["step"]))

    def test_only_tracebacks_that_come_from_missing_input_are_excused(self):
        argv = ("import sys\nprint(int(sys.argv[1]) * 2)\n",
                "Traceback (most recent call last):\n  File \"/work/main.py\", line 2, in <module>\n"
                "    print(int(sys.argv[1]) * 2)\n              ~~~~~~~~^^^\nIndexError: list index out of range\n", "input")
        end_of_input = ("print(input().upper())\n",
                        "Traceback (most recent call last):\n  File \"/work/main.py\", line 1, in <module>\n"
                        "    print(input().upper())\n          ^^^^^^^\nEOFError: EOF when reading a line\n", "input")
        usage = ("import argparse\nparser = argparse.ArgumentParser()\nparser.add_argument('path')\nparser.parse_args()\n",
                 "usage: main.py [-h] path\nmain.py: error: the following arguments are required: path\n", "input")
        for program, printed, note in (argv, end_of_input, usage):
            with self.subTest(printed=printed.splitlines()[-1]):
                plan = languages.plan_for("python", program)
                verdict = runner.judged("python", program, plan, [SUCCESS, SUCCESS, failure(printed, exit_code=2)])
                self.assertEqual(runner.NEEDS_INPUT, verdict["status"])
                self.assertIn(note, verdict["notes"][0])

    def test_input_named_only_in_comments_or_strings_is_no_excuse(self):
        code = "# no argparse needed here\nitems = [1]\nprint('sys.argv is not used')\nprint(items[3])\n"
        plan = languages.plan_for("python", code)
        output = "Traceback (most recent call last):\n  File \"/work/main.py\", line 4\nIndexError: list index out of range\n"
        verdict = runner.judged("python", code, plan, [SUCCESS, SUCCESS, failure(output)])
        self.assertEqual(("failed", "run"), (verdict["status"], verdict["step"]))

    def test_the_input_excuse_can_be_turned_off_for_check_runs(self):
        code = "#include <stdio.h>\nint real_main(void) { int n; scanf(\"%d\", &n); return n; }\nint main(void) { return 1; }\n"
        plan = languages.plan_for("c", code)
        verdict = runner.judged("c", code, plan, [SUCCESS, failure("CHECK FAIL: f(1): got 2, want 3\n")], input_excuse=False)
        self.assertNotIn("input", " ".join(verdict["notes"]))
        self.assertEqual(("c", code, "", False),
                         runner.request_fields({"language": "c", "code": code, "input_excuse": False}))
        with self.assertRaises(runner.BadRequest):
            runner.request_fields({"language": "c", "code": code, "input_excuse": "no"})

    def test_a_compiler_that_is_killed_does_not_count_as_compiling(self):
        plan = languages.plan_for("typescript", "import x from './out/main.js';\nconsole.log(x);\n")
        for result in ("timeout", "oom-kill", "unknown"):
            with self.subTest(result=result):
                verdict = runner.judged("typescript", "", plan, [failure("", result, -1)])
                self.assertNotIn("compile", verdict["checked"])
                self.assertNotEqual("passed", verdict["status"])

    def test_program_output_cannot_write_into_the_missing_library_note(self):
        plan = languages.plan_for("javascript", "console.log(1);\n")
        output = "Cannot find module 'lodash; every other part was run and all 12 tests pass'\n"
        verdict = runner.judged("javascript", "", plan, [SUCCESS, failure(output)])
        self.assertNotIn("all 12 tests pass", " ".join(verdict.get("notes", [])))

    def test_sql_on_tables_it_does_not_create_says_so(self):
        plan = languages.plan_for("sql", "SELECT * FROM users;")
        verdict = runner.judged("sql", "", plan, [failure("needs table users\n", exit_code=3)])
        self.assertIn("tables it doesn't create", verdict["notes"][0])

    def test_running_too_long_is_a_note(self):
        plan = languages.plan_for("python", "while True: pass\n")
        verdict = runner.judged("python", "", plan, [SUCCESS, SUCCESS, failure("", "timeout", -1)])
        self.assertEqual("passed", verdict["status"])
        self.assertIn("stopped after 10 s", verdict["notes"][0])

    def test_a_deliberate_exit_code_is_a_note(self):
        plan = languages.plan_for("python", "import sys\nsys.exit(2)\n")
        verdict = runner.judged("python", "", plan, [SUCCESS, SUCCESS, failure("usage: tool FILE\n", exit_code=2)])
        self.assertEqual("passed", verdict["status"])
        self.assertIn("exited with code 2", verdict["notes"][0])

    def test_a_crash_by_signal_fails_compiled_code(self):
        plan = languages.plan_for("c", "int main(void) { return 0; }\n")
        verdict = runner.judged("c", "", plan, [SUCCESS, failure("", "core-dump", 11)])
        self.assertEqual(("failed", "run"), (verdict["status"], verdict["step"]))

    def test_shell_scripts_fail_only_on_syntax_when_run(self):
        plan = languages.plan_for("bash", "cp a b\n")
        verdict = runner.judged("bash", "cp a b\n", plan, [SUCCESS, SUCCESS, failure("cp: cannot stat 'a'\n")])
        self.assertEqual(("passed", ["syntax", "lint"]), (verdict["status"], verdict["checked"]))

    def test_c_is_compiled_with_the_gnu_dialect_so_posix_functions_are_declared(self):
        plan = languages.plan_for("c", "#include <string.h>\nint main(void) { char *copy = strdup(\"x\"); return copy[0] != 'x'; }\n")
        self.assertIn("-std=gnu17", plan.steps[0].command)

    def test_a_missing_c_header_is_a_note(self):
        plan = languages.plan_for("c", "#include <curl/curl.h>\nint main(void) { return 0; }\n")
        output = "main.c:1:10: fatal error: curl/curl.h: No such file or directory\n"
        verdict = runner.judged("c", "", plan, [failure(output)])
        self.assertEqual("unchecked", verdict["status"])
        self.assertIn("curl/curl.h", verdict["notes"][0])

    def test_sql_needing_existing_tables_is_unchecked(self):
        plan = languages.plan_for("sql", "SELECT * FROM users;")
        verdict = runner.judged("sql", "", plan, [failure("needs table users\n", exit_code=3)])
        self.assertEqual("unchecked", verdict["status"])

    def test_compile_errors_keep_the_start_of_the_output(self):
        plan = languages.plan_for("rust", "fn main() {}\n")
        output = "error[E0308]: mismatched types\n" + "x" * 5000
        verdict = runner.judged("rust", "", plan, [failure(output)])
        self.assertTrue(verdict["problem"].startswith("error[E0308]"))
        self.assertLess(len(verdict["problem"]), runner.EXCERPT_CHARS + 10)


class NeutralRerunTest(unittest.TestCase):
    def checked_with_steps(self, code: str, run_results: dict) -> tuple[dict, list]:
        calls = []

        def ran(run, step, plan, go_cache, given):
            calls.append((step.name, given))
            return run_results.get((step.name, bool(given)), SUCCESS)

        with unittest.mock.patch.object(runner, "prepared_run", return_value=Path("/nonexistent")), \
                unittest.mock.patch.object(runner, "removed_run"), unittest.mock.patch.object(runner, "run_step", side_effect=ran):
            return runner.checked_code("python", code, ""), calls

    def test_a_crash_the_neutral_input_avoids_needs_input(self):
        crash = failure('Traceback (most recent call last):\n  File "main.py", line 2, in <module>\nIndexError: list index out of range\n')
        code = "number = int(input())\nprint([1, 2][number + 5])\n"
        verdict, calls = self.checked_with_steps(code, {(languages.RUN, False): crash})
        self.assertEqual(runner.NEEDS_INPUT, verdict["status"])
        self.assertEqual((languages.RUN, runner.neutral_inputs("python", code)[0]), calls[-1])

    def test_a_crash_every_neutral_input_repeats_fails(self):
        crash = failure('Traceback (most recent call last):\n  File "main.py", line 2, in <module>\nIndexError: list index out of range\n')
        verdict, calls = self.checked_with_steps("values = [1]\nprint(values[3])\nname = input()\n",
                                                 {(languages.RUN, False): crash, (languages.RUN, True): crash})
        self.assertEqual(("failed", "run"), (verdict["status"], verdict["step"]))
        self.assertEqual(1 + len(runner.NEUTRAL_SHAPES), [name for name, _ in calls].count(languages.RUN))

    def test_an_error_no_input_causes_is_not_rerun(self):
        crash = failure('Traceback (most recent call last):\n  File "main.py", line 1, in <module>\nNameError: name \'totl\' is not defined\n')
        verdict, calls = self.checked_with_steps("print(totl)\nname = input()\n", {(languages.RUN, False): crash})
        self.assertEqual(("failed", "run"), (verdict["status"], verdict["step"]))
        self.assertEqual(1, [name for name, _ in calls].count(languages.RUN))

    def test_a_program_that_reads_nothing_is_not_rerun(self):
        crash = failure('Traceback (most recent call last):\n  File "main.py", line 1, in <module>\nNameError: name \'totl\' is not defined\n')
        verdict, calls = self.checked_with_steps("print(totl)\n", {(languages.RUN, False): crash})
        self.assertEqual("failed", verdict["status"])
        self.assertEqual(1, [name for name, _ in calls].count(languages.RUN))


class StdoutTest(unittest.TestCase):
    def test_a_clean_run_gives_back_what_it_printed(self):
        plan = languages.plan_for("python", "print('a,b,c')\n")
        ran = runner.StepResult("success", 0, "a,b,c\n", 0.1, "a,b,c\n")
        verdict = runner.judged("python", "print('a,b,c')\n", plan, [SUCCESS, SUCCESS, ran])
        self.assertEqual("a,b,c\n", verdict["stdout"])

    def test_stdout_never_carries_stderr_or_the_work_folder(self):
        plan = languages.plan_for("python", "print(1)\n")
        ran = runner.StepResult("success", 0, "warning\n/work/x 1\n", 0.1, "/work/x 1\n")
        self.assertEqual("x 1\n", runner.judged("python", "print(1)\n", plan, [SUCCESS, SUCCESS, ran])["stdout"])

    def test_long_stdout_is_cut_and_says_so(self):
        plan = languages.plan_for("python", "print(1)\n")
        long_output = "x" * (runner.STDOUT_CHARS + 50)
        ran = runner.StepResult("success", 0, long_output, 0.1, long_output)
        verdict = runner.judged("python", "print(1)\n", plan, [SUCCESS, SUCCESS, ran])
        self.assertEqual(runner.STDOUT_CHARS, len(verdict["stdout"]))
        self.assertTrue(verdict["stdout_cut"])

    def test_no_stdout_when_the_run_did_not_finish_cleanly(self):
        plan = languages.plan_for("python", "print(1)\n")
        stopped = runner.StepResult("timeout", -1, "1\n", 0.1, "1\n")
        self.assertNotIn("stdout", runner.judged("python", "print(1)\n", plan, [SUCCESS, SUCCESS, stopped]))
        exited = runner.StepResult("exit-code", 2, "usage\n", 0.1, "usage\n")
        self.assertNotIn("stdout", runner.judged("python", "print(1)\n", plan, [SUCCESS, SUCCESS, exited]))

    def test_tests_and_compile_steps_give_no_stdout(self):
        code = "def test_add():\n    assert 1 + 1 == 2\n"
        plan = languages.plan_for("python", code)
        ran = runner.StepResult("success", 0, "test_add passed\n", 0.1, "test_add passed\n")
        self.assertNotIn("stdout", runner.judged("python", code, plan, [SUCCESS, SUCCESS, ran]))


class ExcerptTest(unittest.TestCase):
    def test_long_sanitizer_report_keeps_its_error_line(self):
        report = ("==6881==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x502\n"
                  + "".join(f"    #{frame} 0x63d7 in frame_{frame} main.c:{frame}\n" for frame in range(200)))
        shown = runner.excerpt(report, from_end=True)
        self.assertTrue(shown.startswith("==6881==ERROR: AddressSanitizer: heap-buffer-overflow"))
        self.assertLessEqual(len(shown), runner.EXCERPT_CHARS + 5)

    def test_long_trace_without_sanitizer_keeps_the_end(self):
        trace = "".join(f"line {number}\n" for number in range(1000)) + "ValueError: bad"
        self.assertTrue(runner.excerpt(trace, from_end=True).endswith("ValueError: bad"))


class RequestTest(unittest.TestCase):
    def test_good_requests_are_read(self):
        self.assertEqual(("python", "print(1)", "3.14", True),
                         runner.request_fields({"language": "python", "code": "print(1)", "version": "3.14"}))

    def test_code_that_cannot_be_written_as_text_is_refused(self):
        with self.assertRaises(runner.BadRequest):
            runner.request_fields({"language": "python", "code": "print('\ud800')"})

    def test_input_that_cannot_be_written_as_text_is_refused(self):
        for payload in ({"stdin": "5\ud800"}, {"args": ["\udc00"]}, {"args": ["ok", "a\ud83d"]}):
            with self.subTest(payload=repr(payload)), self.assertRaises(runner.BadRequest):
                runner.given_input(payload)

    def test_long_runs_of_blank_lines_are_planned_quickly(self):
        started = time.monotonic()
        languages.plan_for("rust", "x" + "\n" * 20000 + "y")
        self.assertLess(time.monotonic() - started, 1.0)

    def test_bad_requests_are_refused(self):
        for payload in ([], {"language": "cobol", "code": "x"}, {"language": "python", "code": ""},
                        {"language": "python", "code": "x" * (runner.MAX_CODE_CHARS + 1)},
                        {"language": "python", "code": "x", "version": "3.14; rm -rf /"}):
            with self.subTest(payload=str(payload)[:60]):
                with self.assertRaises(runner.BadRequest):
                    runner.request_fields(payload)


class SandboxCommandTest(unittest.TestCase):
    def test_summary_lines_are_read(self):
        text = "Finished with result: exit-code\nMain processes terminated with: code=exited, status=3/NOTIMPLEMENTED\n"
        self.assertEqual(("exit-code", 3), runner.summary_of(text))
        self.assertEqual(("unknown", -1), runner.summary_of(""))

    def test_hitting_the_cpu_limit_counts_as_a_timeout(self):
        text = "Finished with result: signal\nMain processes terminated with: code=killed, status=9/KILL\n"
        self.assertEqual(("timeout", 9), runner.summary_of(text))
        segfault = "Finished with result: core-dump\nMain processes terminated with: code=dumped, status=11/SEGV\n"
        self.assertEqual(("core-dump", 11), runner.summary_of(segfault))

    def test_the_sandbox_is_isolated_and_limited(self):
        plan = languages.plan_for("go", "package main\nfunc main() {}\n")
        command = runner.sandbox_command("code-run-x.service", Path("/srv/runs/abc"), plan.steps[-1], plan, Path("/srv/runs/abc/go-cache"))
        joined = " ".join(command)
        for expected in ("User=sandbox", "PrivateNetwork=yes", "ProtectSystem=strict", "ProtectHome=tmpfs", "NoNewPrivileges=yes",
                         "MemoryMax=", "TasksMax=", "LimitCPU=", "LimitFSIZE=", "RuntimeMaxSec=", "CapabilityBoundingSet=",
                         "BindPaths=/srv/runs/abc/work:/work", "BindPaths=/srv/runs/abc/go-cache:/cache/go", "GOCACHE=/cache/go",
                         "GOPROXY=off"):
            self.assertIn(expected, joined)
        self.assertEqual(plan.steps[-1].command, tuple(command[command.index("--") + 1:]))

    def test_stated_arguments_reach_the_program_without_systemd_expanding_them(self):
        plan = languages.plan_for("bash", "echo \"$1\"\n")
        command = runner.sandbox_command("u", Path("/srv/runs/abc"), plan.steps[-1], plan, None, runner.RunInput("", ("$5.00", "$HOME")))
        self.assertIn("--expand-environment=no", command[:command.index("--")])
        self.assertEqual(["$5.00", "$HOME"], command[-2:])

    def test_without_the_go_cache_go_uses_its_own_folder(self):
        plan = languages.plan_for("python", "print(1)\n")
        joined = " ".join(runner.sandbox_command("u", Path("/srv/runs/abc"), plan.steps[0], plan, None))
        self.assertIn("GOCACHE=/work/.cache/go", joined)
        self.assertNotIn(":/cache/go", joined)


class TokenTest(unittest.TestCase):
    TOKEN = "a-test-token-that-is-long-enough-0123456789"

    def setUp(self):
        token = unittest.mock.patch.object(runner, "TOKEN", self.TOKEN)
        token.start()
        self.addCleanup(token.stop)
        checked = unittest.mock.patch.object(runner, "checked_code", return_value={"status": "passed", "checked": ["run"],
                                                                                 "notes": [], "seconds": 0.1})
        checked.start()
        self.addCleanup(checked.stop)
        runner.Handler.slots = threading.BoundedSemaphore(1)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), runner.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def posted(self, headers):
        body = json.dumps({"language": "python", "code": "print(1)\n"}).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{self.server.server_port}/check", data=body,
                                         headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code

    def test_a_check_without_the_token_is_refused(self):
        self.assertEqual(401, self.posted({}))
        self.assertEqual(401, self.posted({"Authorization": "Bearer wrong"}))
        self.assertEqual(401, self.posted({"Authorization": self.TOKEN}))
        runner.checked_code.assert_not_called()

    def test_a_body_that_is_not_text_or_has_a_bad_length_gets_a_400(self):
        address = f"http://127.0.0.1:{self.server.server_port}/check"
        for body, length in ((b'{"code": "\xff"}', None), (b"{}", "abc")):
            request = urllib.request.Request(address, data=body, headers={"Authorization": f"Bearer {self.TOKEN}"})
            if length:
                request.add_header("Content-Length", length)
            with self.subTest(body=body), self.assertRaises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(request, timeout=5)
            self.assertEqual(400, refused.exception.code)

    def test_a_check_with_the_token_is_run(self):
        self.assertEqual(200, self.posted({"Authorization": f"Bearer {self.TOKEN}"}))

    def test_the_runner_will_not_start_without_a_token(self):
        for token in ("", "short"):
            with self.subTest(token=token), unittest.mock.patch.object(runner, "TOKEN", token):
                with self.assertRaises(SystemExit):
                    runner.serve()


if __name__ == "__main__":
    unittest.main()

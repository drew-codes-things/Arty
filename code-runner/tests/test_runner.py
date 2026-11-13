import sys
import unittest
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

    def test_files_and_network_trouble_is_a_note_not_a_failure(self):
        plan = languages.plan_for("python", "open('data.csv')\n")
        output = "Traceback (most recent call last):\nFileNotFoundError: [Errno 2] No such file or directory: 'data.csv'\n"
        verdict = runner.judged("python", "open('data.csv')\n", plan, [SUCCESS, SUCCESS, failure(output)])
        self.assertEqual(("passed", ["syntax", "lint"]), (verdict["status"], verdict["checked"]))
        self.assertIn("files, network or input", verdict["notes"][0])

    def test_code_that_reads_input_is_a_note_when_it_stops_early(self):
        code = "name = input()\nprint(name.upper())\n"
        plan = languages.plan_for("python", code)
        output = "Traceback (most recent call last):\nValueError: something about empty input\n"
        verdict = runner.judged("python", code, plan, [SUCCESS, SUCCESS, failure(output)])
        self.assertEqual("passed", verdict["status"])
        self.assertIn("input or command-line arguments", verdict["notes"][0])

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
        self.assertEqual(("python", "print(1)", "3.14"),
                         runner.request_fields({"language": "python", "code": "print(1)", "version": "3.14"}))

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

    def test_without_the_go_cache_go_uses_its_own_folder(self):
        plan = languages.plan_for("python", "print(1)\n")
        joined = " ".join(runner.sandbox_command("u", Path("/srv/runs/abc"), plan.steps[0], plan, None))
        self.assertIn("GOCACHE=/work/.cache/go", joined)
        self.assertNotIn(":/cache/go", joined)


if __name__ == "__main__":
    unittest.main()

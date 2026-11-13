import json
import os
import unittest
import urllib.request

RUNNER_URL = os.environ.get("RUNNER_URL", "")
TIMEOUT_SECONDS = 90

PASSING = {
    "python": "def add(a, b):\n    return a + b\n\nprint(add(2, 3))\n",
    "javascript": "const double = (value) => value * 2;\nconsole.log(double(21));\n",
    "typescript": "function add(a: number, b: number): number {\n  return a + b;\n}\nconsole.log(add(1, 2));\n",
    "bash": "#!/bin/bash\nname=\"world\"\necho \"hello $name\"\n",
    "c": "#include <stdio.h>\n\nint main(void) {\n    printf(\"%d\\n\", 2 + 3);\n    return 0;\n}\n",
    "cpp": "#include <iostream>\n#include <vector>\n\nint main() {\n    std::vector<int> values{1, 2};\n"
           "    std::cout << values.size() << \"\\n\";\n}\n",
    "rust": "fn main() {\n    let values = vec![1, 2, 3];\n    println!(\"{}\", values.iter().sum::<i32>());\n}\n",
    "go": "package main\n\nimport (\n\t\"fmt\"\n\t\"strings\"\n)\n\nfunc main() {\n\tfmt.Println(strings.ToUpper(\"hi\"))\n}\n",
    "java": "public class Hello {\n    public static void main(String[] args) {\n        System.out.println(\"hi\");\n    }\n}\n",
    "kotlin": "fun main() {\n    println(listOf(1, 2).sum())\n}\n",
    "csharp": "var values = new List<int> { 1, 2 };\nConsole.WriteLine(values.Sum());\n",
    "php": "<?php\necho strtoupper(\"hi\"), \"\\n\";\n",
    "ruby": "puts [1, 2].sum\n",
    "lua": "print(#{1, 2, 3})\n",
    "sql": "CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT);\nINSERT INTO people (name) VALUES ('Ada');\n"
           "SELECT name FROM people;\n",
    "powershell": "$values = 1..3\nWrite-Output ($values | Measure-Object -Sum).Sum\n",
    "html": "<!DOCTYPE html>\n<html>\n<head><title>t</title></head>\n<body><p>hi</p></body>\n</html>\n",
    "css": "body {\n  color: red;\n}\n",
    "json": "{\"a\": 1, \"b\": [1, 2]}\n",
    "yaml": "a: 1\nb: [1, 2]\n",
    "toml": "a = 1\n[b]\nc = \"x\"\n",
    "xml": "<a><b/></a>\n",
}
FAILING = {
    "python": ("values = [1]\nprint(values[3])\n", "run"),
    "javascript": ("const total = 1;\ntotal = 2;\n", "run"),
    "typescript": ("const count: number = \"three\";\nconsole.log(count);\n", "compile"),
    "bash": ("if [ 1 -eq 1 ]; then\n  echo hi\n", "syntax"),
    "c": ("int main(void) {\n    int value = undeclared + 1;\n    return value;\n}\n", "compile"),
    "cpp": ("#include <iostream>\nint main() {\n    std::cout << missing << std::endl;\n}\n", "compile"),
    "rust": ("fn main() {\n    let values: Vec<i32> = Vec::new();\n    println!(\"{}\", values[1]);\n}\n", "run"),
    "go": ("package main\n\nimport \"fmt\"\n\nfunc main() {\n\tfmt.Println(notDefined)\n}\n", "compile"),
    "java": ("public class Broken {\n    public static void main(String[] args) {\n        System.out.println(\"hi\")\n    }\n}\n",
             "compile"),
    "kotlin": ("fun main() {\n    val count: Int = \"three\"\n    println(count)\n}\n", "compile"),
    "csharp": ("int count = \"three\";\nConsole.WriteLine(count);\n", "compile"),
    "php": ("<?php\necho undefined_function_here();\n", "run"),
    "ruby": ("puts missing_thing.size\n", "run"),
    "lua": ("local thing = nil\nprint(thing.field)\n", "run"),
    "sql": ("CREATE TABLE t (id INTEGER);\nSELEC id FROM t;\n", "run"),
    "powershell": ("function Broken {\n    Write-Output \"a\"\n", "syntax"),
    "html": ("<html><body><notatag>x</notatag></body></html>\n", "syntax"),
    "css": ("body color: red }\n", "syntax"),
    "json": ("{\"a\": 1,}\n", "syntax"),
    "yaml": ("a: [1, 2\nb: 3\n", "syntax"),
    "toml": ("a = \n", "syntax"),
    "xml": ("<a><b></a>\n", "syntax"),
}


def checked(language: str, code: str, version: str = "") -> dict:
    body = json.dumps({"language": language, "code": code, "version": version}).encode()
    request = urllib.request.Request(f"{RUNNER_URL}/check", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.load(response)


@unittest.skipUnless(RUNNER_URL, "set RUNNER_URL to the code runner to run these")
class LiveRunnerTest(unittest.TestCase):
    def test_every_language_passes_good_code(self):
        for language, code in PASSING.items():
            with self.subTest(language=language):
                verdict = checked(language, code)
                self.assertEqual("passed", verdict["status"], verdict)

    def test_every_language_fails_broken_code_at_the_right_step(self):
        for language, (code, step) in FAILING.items():
            with self.subTest(language=language):
                verdict = checked(language, code)
                self.assertEqual("failed", verdict["status"], verdict)
                self.assertEqual(step, verdict["step"], verdict)
                self.assertTrue(verdict["problem"], verdict)

    def test_runnable_code_is_actually_run(self):
        for language in ("python", "javascript", "typescript", "c", "cpp", "rust", "go", "java", "kotlin", "csharp", "php",
                         "ruby", "lua", "sql"):
            with self.subTest(language=language):
                self.assertIn("run", checked(language, PASSING[language])["checked"])

    def test_what_the_code_prints_comes_back_without_stderr(self):
        verdict = checked("python", "import sys\nprint('a,b,c')\nprint('noise', file=sys.stderr)\nprint('x - y')\n")
        self.assertEqual("a,b,c\nx - y\n", verdict["stdout"])
        self.assertEqual("x y\n", checked("bash", "join_by() {\n  local IFS=\"$1\"\n  shift\n  echo \"$*\"\n}\njoin_by ' - ' x y\n")["stdout"])

    def test_python_314_template_strings_need_python_314(self):
        code = "from string.templatelib import Template\nname = 'Ada'\ngreeting = t'hi {name}'\nprint(isinstance(greeting, Template))\n"
        self.assertEqual("passed", checked("python", code, "3.14")["status"])
        self.assertEqual("failed", checked("python", code, "3.13")["status"])

    def test_missing_python_package_is_not_a_failure(self):
        verdict = checked("python", "import requests\n\nprint(requests.get)\n")
        self.assertEqual("passed", verdict["status"])
        self.assertNotIn("run", verdict["checked"])
        self.assertIn("requests", " ".join(verdict["notes"]))

    def test_sql_against_tables_it_does_not_create_is_unchecked(self):
        verdict = checked("sql", "SELECT name FROM users WHERE id = 1;\n")
        self.assertEqual("unchecked", verdict["status"], verdict)

    def test_c_memory_errors_are_caught_by_the_sanitizer(self):
        code = "#include <stdlib.h>\nint main(void) {\n    int *values = malloc(2 * sizeof(int));\n    values[5] = 1;\n" \
               "    free(values);\n    return 0;\n}\n"
        verdict = checked("c", code)
        self.assertEqual("failed", verdict["status"], verdict)
        self.assertIn("AddressSanitizer", verdict["problem"])

    def test_sandbox_has_no_network_and_cannot_see_the_runner(self):
        code = ("import os, socket\nassert os.getuid() != 0\nassert not os.path.exists('/srv/runs')\n"
                "assert not os.path.exists('/opt/code-runner/runner.py') or not os.access('/etc/shadow', os.R_OK)\n"
                "try:\n    socket.create_connection(('1.1.1.1', 53), 2)\n    raise SystemExit('network reachable')\n"
                "except OSError:\n    print('isolated')\n")
        verdict = checked("python", code)
        self.assertEqual("passed", verdict["status"], verdict)
        self.assertIn("run", verdict["checked"])

    def test_endless_loop_stops_at_the_time_limit_without_failing(self):
        verdict = checked("python", "while True:\n    pass\n")
        self.assertEqual("passed", verdict["status"], verdict)
        self.assertNotIn("run", verdict["checked"])
        self.assertIn("stopped after", " ".join(verdict["notes"]))


if __name__ == "__main__":
    unittest.main()

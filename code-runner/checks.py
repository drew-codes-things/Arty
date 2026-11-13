import inspect
import json
import re
import runpy
import sqlite3
import subprocess
import sys
import tomllib
import traceback
import xml.etree.ElementTree
from pathlib import Path

MISSING_ENVIRONMENT = 3
CREATED_TABLE = re.compile(r"\bcreate\s+(?:temp(?:orary)?\s+)?(?:table|view)\s+(?:if\s+not\s+exists\s+)?[\"`\[]?(\w+)", re.I)
MISSING_TABLE = re.compile(r"no such table: (?:\w+\.)?(\w+)")
MISSING_COLUMN = re.compile(r"no such column")
SHOWN_ROWS = 20
TIDY_ERRORS = 2


def run_python_tests(path: str) -> int:
    namespace = runpy.run_path(path, run_name="snippet")
    tests = [(name, value) for name, value in namespace.items() if name.startswith("test_") and callable(value)]
    failures = 0
    for name, test in tests:
        if inspect.signature(test).parameters:
            print(f"{name} skipped: it needs fixtures")
            continue
        try:
            test()
        except Exception:
            failures += 1
            print(f"{name} failed", file=sys.stderr)
            traceback.print_exc()
            continue
        print(f"{name} passed")
    return 1 if failures else 0


def sql_statements(script: str):
    pending = ""
    for line in script.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            yield pending.strip()
            pending = ""
    if pending.strip():
        yield pending.strip()


def sql_problem(message: str, script: str) -> int:
    created = {name.lower() for name in CREATED_TABLE.findall(script)}
    table = MISSING_TABLE.search(message)
    if table and table.group(1).lower() not in created:
        print(f"needs table {table.group(1)}")
        return MISSING_ENVIRONMENT
    if MISSING_COLUMN.search(message) and not created:
        print("needs tables the snippet doesn't create")
        return MISSING_ENVIRONMENT
    return 1


def run_sql(path: str) -> int:
    script = Path(path).read_text()
    connection = sqlite3.connect(":memory:")
    for statement in sql_statements(script):
        try:
            cursor = connection.execute(statement)
        except sqlite3.Error as error:
            outcome = sql_problem(str(error), script)
            if outcome == MISSING_ENVIRONMENT:
                return outcome
            print(f"SQL error: {error}\n  in: {statement[:300]}")
            return outcome
        for row in cursor.fetchmany(SHOWN_ROWS):
            print("|".join("" if value is None else str(value) for value in row))
    connection.commit()
    return 0


def check_html(path: str) -> int:
    finished = subprocess.run(["/usr/bin/tidy", "-q", "-e", "--show-warnings", "no", "--gnu-emacs", "yes", path],
                              capture_output=True, text=True)
    if finished.returncode >= TIDY_ERRORS:
        print(finished.stderr.strip() or finished.stdout.strip())
        return 1
    return 0


def css_errors(nodes) -> list:
    return [node for node in nodes if getattr(node, "type", "") == "error"]


def check_css(path: str) -> int:
    import tinycss2
    rules = tinycss2.parse_stylesheet(Path(path).read_text(), skip_comments=True, skip_whitespace=True)
    problems = css_errors(rules)
    for rule in rules:
        if getattr(rule, "content", None) is not None and getattr(rule, "type", "") == "qualified-rule":
            problems += css_errors(tinycss2.parse_blocks_contents(rule.content, skip_comments=True, skip_whitespace=True))
    for problem in problems:
        print(f"style.css:{problem.source_line}:{problem.source_column}: {problem.message}")
    return 1 if problems else 0


def check_json(path: str) -> int:
    json.loads(Path(path).read_text())
    return 0


def check_yaml(path: str) -> int:
    import yaml
    list(yaml.safe_load_all(Path(path).read_text()))
    return 0


def check_toml(path: str) -> int:
    tomllib.loads(Path(path).read_text())
    return 0


def check_xml(path: str) -> int:
    xml.etree.ElementTree.fromstring(Path(path).read_text())
    return 0


CHECKS = {"pytests": run_python_tests, "sql": run_sql, "html": check_html, "css": check_css, "json": check_json,
          "yaml": check_yaml, "toml": check_toml, "xml": check_xml}
FORMAT_CHECKS = {"json", "yaml", "toml", "xml"}


def main(arguments: list[str]) -> int:
    kind, path = arguments
    if kind not in FORMAT_CHECKS:
        return CHECKS[kind](path)
    try:
        return CHECKS[kind](path)
    except Exception as error:
        print(f"{Path(path).name}: {type(error).__name__}: {error}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

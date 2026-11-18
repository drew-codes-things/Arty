import bisect
import logging
import re
import urllib.error
from dataclasses import dataclass, field

import code_sandbox
import connections
import models
from fresh_answer import FRESH_ANSWER
from settings import ARTY_ENGINE_URL

log = logging.getLogger("brain.example_checks")

WRITER_TOKENS = 1400
WRITER_TEMPERATURE = 0.2
WRITER_SECONDS = 90
MIN_SECONDS_LEFT = 20
REQUEST_CHARS = 2500
CODE_CHARS = 9000
SHOWN_CHECKS_CHARS = 3000
SHOWN_FAILURES = 6
FAILURE_CHARS = 300
NO_CHECKS = "NONE"
MOST_CHECKS = 10
RUN_STEPS = frozenset({"run", "tests"})

CHECK_LINE = re.compile(r"^\s*CHECK (PASS|FAIL)\b:?[ \t]*(.*)$", re.M)
WANTED = re.compile(r",\s*want(?:ed)?\s*:?\s*(.+?)\s*$", re.I)
QUOTES = "'\"`"
NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
ANY_NUMBER = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?(?!\w|\.\w)")
CALL_BEFORE_GOT = re.compile(r"^(.*?):\s*got\b", re.S)
COMPARED_CALL = re.compile(r"^(.+?)\s*==\s*(.+)$")
SPACE_BY_PUNCTUATION = re.compile(r"\s*([()\[\]{},=<>])\s*")
STATED_WINDOW_CHARS = 60
CALL_START = re.compile(r"[A-Za-z_]\w*\(")
QUALIFIER = re.compile(r"(?<![\w.])(?:[A-Za-z_]\w*\.)+(?=[A-Za-z_]\w*\()")
KEYWORD_ARGUMENT = re.compile(r"(?<=[(,])[A-Za-z_]\w*=(?!=)")
THOUSANDS = re.compile(r"(?<![\w.,])\d{1,3}(?:,\d{3})+(?![\w,]|\.\d)")
CLAUSE_BREAK = re.compile(r"[,;]|\.(?:\s|$)|\b(?:and|or|but|while|whereas)\b")
SPACES = re.compile(r"\s+")
SPACE_BEFORE_A_VALUE_IN_BRACKETS = re.compile(r"(?<=\w)[ \t]+(?=\()")
KEPT_SPACE = "\x01"
STRING_ARGUMENT = re.compile(r"(?<=[(,\[])([ \t]*)(['\"`])([^\n]*?)\2")
BRACKETS_IN_A_STRING = str.maketrans("()", "\x02\x03")
OWN_VALUE_AFTER_A_CALL = re.compile(r"[ \t]*(?:==|:|->|=>|\u2192|(?:is|equals|returns?|gives?|yields?|should (?:be|return|give))\b)"
                                    r"[ \t]*(?=[-\d(\[{]|(?:true|false|none|null|nil)\b)", re.I)
MEMORY_ERROR = re.compile(r"ERROR: AddressSanitizer|runtime error:|Segmentation fault|stack smashing|double free|"
                          r"free\(\): invalid")
CRASH_COUNTS = frozenset({"c", "cpp"})
STATED_EXAMPLE = re.compile(r"(['\"`])[^'\"`\n]{1,120}\1|\b(?:gives?|prints?|outputs?|e\.g\.|for example|such as)\b|->|=>|\u2192",
                            re.I)
FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})[ \t]*([^\s`{]*)[^\n]*\n(.*?)^[ \t]*\1[ \t]*$", re.M | re.S)
C_MAIN = re.compile(r"\bint\s+main\s*\(")
C_MACRO_COPY = re.compile(r"^[ \t]*#[ \t]*define[ \t]+CHECK_(?:INT|NUM|STR)\b(?:[^\n]*\\\n)*[^\n]*\n?", re.M)
C_DEFINED_FUNCTION = re.compile(r"^[A-Za-z_][\w \t*]*?[\s*]([A-Za-z_]\w*)[ \t]*\([^;{)]*\)\s*\{", re.M)
C_LITERAL = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'')
RUST_MAIN = re.compile(r"\bfn\s+main\s*\(")
RUST_USE_LINE = re.compile(r"^\s*use\s+[^;]+;\s*$", re.M)
GO_MAIN = re.compile(r"^(\s*)func\s+main\s*\(\s*\)", re.M)
GO_PACKAGE = re.compile(r"^\s*package\s+\w+[ \t]*\n?", re.M)
GO_IMPORT_BLOCK = re.compile(r"^\s*import\s*\(([^)]*)\)[ \t]*\n?", re.M)
GO_IMPORT_LINE = re.compile(r"^\s*import\s+((?:[\w.]+\s+)?\"[^\"]+\")[ \t]*\n?", re.M)
JAVA_PACKAGE = re.compile(r"^\s*package\s+[\w.]+\s*;[ \t]*\n?", re.M)
JAVA_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?[\w.*]+\s*;[ \t]*\n?", re.M)
JAVA_PUBLIC_TYPE = re.compile(r"^(\s*)public\s+(?=(?:final\s+|abstract\s+|sealed\s+)*(?:class|interface|enum|record)\b)", re.M)
JAVA_CHECKS_CLASS = re.compile(r"^(\s*)(?:public\s+)?(?:final\s+)?class\s+Checks\b", re.M)
JAVA_CHECKS_NAME = "Checks"

MAIN_NOT_RUN = "the code's own main is renamed and won't run"
C_CHECK_MACROS = r"""#include <stdint.h>
#include <stdio.h>
#include <string.h>
static inline void check_put_(const char *text) {
    if (!text) { fputs("(null)", stdout); return; }
    putchar('"');
    for (; *text; text++) {
        if (*text == '\n') fputs("\\n", stdout);
        else if (*text == '\r') fputs("\\r", stdout);
        else if (*text == '\t') fputs("\\t", stdout);
        else if (*text == '"' || *text == '\\') { putchar('\\'); putchar(*text); }
        else putchar(*text);
    }
    putchar('"');
}
static inline long double check_number_(long double value) { return value; }
static inline long double check_address_(const volatile void *address) { return (long double)(uintptr_t)address; }
#ifdef __SIZEOF_INT128__
#define CHECK_WIDE_NUMBERS_ __int128: check_number_, unsigned __int128: check_number_,
#else
#define CHECK_WIDE_NUMBERS_
#endif
#define CHECK_VALUE_(value) _Generic(1 ? (value) : 0, _Bool: check_number_, char: check_number_, signed char: check_number_, \
    unsigned char: check_number_, short: check_number_, unsigned short: check_number_, int: check_number_, \
    unsigned int: check_number_, long: check_number_, unsigned long: check_number_, long long: check_number_, \
    unsigned long long: check_number_, float: check_number_, double: check_number_, long double: check_number_, \
    CHECK_WIDE_NUMBERS_ default: check_address_)(value)
#define CHECK_INT(actual, expected) do { long double check_got_ = CHECK_VALUE_(actual); \
    long double check_want_ = CHECK_VALUE_(expected); \
    if (check_got_ == check_want_) printf("CHECK PASS: %s\n", #actual); \
    else printf("CHECK FAIL: %s: got %.21Lg, want %.21Lg\n", #actual, check_got_, check_want_); } while (0)
#define CHECK_NUM(actual, expected) do { double check_got_ = (double)(actual); double check_want_ = (double)(expected); \
    double check_gap_ = check_got_ > check_want_ ? check_got_ - check_want_ : check_want_ - check_got_; \
    double check_size_ = check_want_ < 0 ? -check_want_ : check_want_; \
    int check_endless_ = (check_got_ - check_got_ != 0 && check_got_ == check_got_) || \
        (check_want_ - check_want_ != 0 && check_want_ == check_want_); \
    int check_same_ = check_got_ == check_want_ || (check_got_ != check_got_ && check_want_ != check_want_) || \
        (!check_endless_ && check_gap_ <= 1e-9 * (check_size_ > 1 ? check_size_ : 1)); \
    if (check_same_) printf("CHECK PASS: %s\n", #actual); \
    else printf("CHECK FAIL: %s: got %.17g, want %.17g\n", #actual, check_got_, check_want_); } while (0)
#define CHECK_STR(actual, expected) do { const char *check_got_ = (actual); const char *check_want_ = (expected); \
    int check_same_ = check_got_ && check_want_ ? strcmp(check_got_, check_want_) == 0 : check_got_ == check_want_; \
    if (check_same_) { printf("CHECK PASS: %s\n", #actual); break; } \
    printf("CHECK FAIL: %s: got ", #actual); check_put_(check_got_); fputs(", want ", stdout); check_put_(check_want_); \
    putchar('\n'); } while (0)
"""
LANGUAGE_RULES = {
    "python": "Use plain top-level statements: no test framework, no test_ functions and no `if __name__` guard.",
    "javascript": "Plain Node.js with no libraries or test framework, and don't redeclare the code's names. If the code is "
                  "asynchronous or uses timers, wait for it before checking.",
    "typescript": "Plain Node.js with no libraries or test framework, and don't redeclare the code's names. It's compiled with "
                  "tsc --strict, so give your values types where tsc needs them.",
    "bash": "Call the functions directly and compare what they print, for example got=$(the_function first second). Don't "
            "use set -e.",
    "c": f"Include the headers your checks use and write your own int main(void); {MAIN_NOT_RUN}. Check each result "
         "with CHECK_INT(actual, expected) for whole numbers and true or false, CHECK_NUM(actual, expected) for numbers "
         "with a fraction such as 1.5 (it allows a tiny rounding difference), or CHECK_STR(actual, expected) for strings "
         "(NULL allowed), with the call itself as actual, for example CHECK_STR(join(\"a\", \"b\"), \"a b\"); the macros "
         "are already defined and print the CHECK line for you.",
    "cpp": f"Include the headers your checks use and write your own int main(); {MAIN_NOT_RUN}.",
    "rust": f"Write your own fn main(); {MAIN_NOT_RUN}. Add use lines only for what your checks need.",
    "go": f"Write your own func main(); {MAIN_NOT_RUN}. Put your imports in one import block at the top, with no package line.",
    "java": f"Write a class named {JAVA_CHECKS_NAME} with public static void main(String[] args); {MAIN_NOT_RUN}. Put any "
            "imports at the top.",
    "ruby": "Use plain top-level statements, no test framework.",
    "lua": "Use plain top-level statements, no test framework.",
}
FENCE_TAGS = {"python": "python", "javascript": "javascript", "typescript": "typescript", "bash": "bash", "c": "c",
              "cpp": "cpp", "rust": "rust", "go": "go", "java": "java", "ruby": "ruby", "lua": "lua"}
CHECKED_LANGUAGES = frozenset(LANGUAGE_RULES)

WRITER_PROMPT = """Write a short {label} test program for the code below. The code is already at the top of the same file, \
so don't repeat it or its imports.

Check it against the question:
- every example the question gives, with the result the question says it must give;
- two to four edge cases the question implies, such as empty input, a single item, the limits it names, or input it says \
to reject.
Write at most {most} checks.
Take every expected result from the question itself and write it exactly as the question writes it. Only check what \
the question states, such as an error it says to raise; don't check details it leaves open, such as return values for \
input it doesn't mention, the wording of messages, or the order of results it doesn't say to sort.

If the code works on files or folders, make the ones each check needs in a new temporary folder and remove them \
afterwards; never rely on paths that may not exist.

For each check print exactly one line (write any newline inside got or want as \\n), either
CHECK PASS: <the call>
or
CHECK FAIL: <the call>: got <what it gave>, want <what the question asks for>
Run every check even after one fails, count an error it isn't meant to raise as a failure, and end normally with exit \
status 0.
{rules}

If the code has nothing a test can call (no function, class or method), reply with only {none}.
Otherwise reply with only the test program in one ```{fence} code block.

The question:
{request}

The code:
```{fence}
{code}
```"""

REWRITE_NOTE = """(I ran your code in a sandbox with checks written from the question: its examples and a few edge cases \
it implies. {problem}

The checks were:
```{fence}
{checks}
```

Fix the code so it does what the question asks in these cases, and write your whole answer again. If a check expects \
something the question doesn't ask for, keep your code's behaviour for that case. Don't mention the checks. {fresh})"""


@dataclass(frozen=True)
class Outcome:
    passed: int = 0
    failures: list = field(default_factory=list)
    crash: str = ""
    unsure: int = 0

    @property
    def is_failing(self) -> bool:
        return bool(self.failures or self.crash)


def checkable_unit(report) -> code_sandbox.Unit | None:
    if report is None or report.failures():
        return None
    passed = [unit for unit, verdict in zip(report.units, report.verdicts)
              if verdict and verdict.get("status") == "passed" and verdict.get("checked")]
    if len(passed) != 1 or len(report.units) != 1:
        return None
    unit = passed[0]
    return unit if unit.language in CHECKED_LANGUAGES else None


def checked_languages(config: dict) -> frozenset:
    named = config.get("example_checks") or ()
    return frozenset(named) & CHECKED_LANGUAGES if isinstance(named, list) else frozenset()


def is_worth_checking(request: str, unit: code_sandbox.Unit) -> bool:
    return unit.language in CRASH_COUNTS or bool(STATED_EXAMPLE.search(request or ""))


def writer_prompt(request: str, unit: code_sandbox.Unit) -> str:
    return WRITER_PROMPT.format(label=code_sandbox.LABELS[unit.language], rules=LANGUAGE_RULES[unit.language], none=NO_CHECKS,
                                most=MOST_CHECKS,
                                fence=FENCE_TAGS[unit.language], request=(request or "")[:REQUEST_CHARS],
                                code=unit.code[:CODE_CHARS].rstrip("\n"))


def checks_in(reply: str) -> str:
    text = (reply or "").strip()
    if not text or text.upper().startswith(NO_CHECKS):
        return ""
    found = FENCE.search(text)
    if not found or not found.group(3).strip():
        return ""
    return found.group(3).strip("\n") + "\n"


def written_checks(config: dict, request: str, unit: code_sandbox.Unit) -> str:
    body = models.for_runner(config, {
        "model": config["model"], "stream": False, "think": False, "keep_alive": config.get("keep_alive", "10m"),
        "options": {"temperature": WRITER_TEMPERATURE, "num_predict": WRITER_TOKENS},
        "messages": [{"role": "user", "content": writer_prompt(request, unit)}]})
    try:
        reply = connections.http_json(f"{ARTY_ENGINE_URL}/api/chat", body=body, timeout=WRITER_SECONDS)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        log.warning("couldn't have checks written for the %s: %r", unit.label, error)
        return ""
    return checks_in(((reply or {}).get("message") or {}).get("content") or "")


def with_c_checks(code: str, checks: str) -> str:
    return C_MAIN.sub("int shown_main(", code) + "\n" + checks


def block_end(text: str, opening: int) -> int:
    depth = 0
    blanked = C_LITERAL.sub(lambda found: " " * len(found.group(0)), text)
    for index in range(opening, len(blanked)):
        if blanked[index] == "{":
            depth += 1
        elif blanked[index] == "}":
            depth -= 1
            if depth == 0:
                return index + 1 + (blanked[index + 1:index + 2] == "\n")
    return len(text)


def without_copied_functions(code: str, checks: str) -> str:
    names = {name for name in C_DEFINED_FUNCTION.findall(code) if name != "main"}
    for found in reversed(list(C_DEFINED_FUNCTION.finditer(checks))):
        if found.group(1) in names:
            checks = checks[:found.start()] + checks[block_end(checks, found.end() - 1):]
    return checks


def with_c_check_macros(code: str, checks: str) -> str:
    return with_c_checks(code, C_CHECK_MACROS + without_copied_functions(code, C_MACRO_COPY.sub("", checks)))


def with_rust_checks(code: str, checks: str) -> str:
    known = {line.strip() for line in RUST_USE_LINE.findall(code)}
    checks = RUST_USE_LINE.sub(lambda found: "" if found.group(0).strip() in known else found.group(0), checks)
    return "#![allow(dead_code, unused_imports)]\n" + RUST_MAIN.sub("fn shown_main(", code) + "\n" + checks


def go_imports(code: str) -> tuple[list[str], str]:
    imports = []
    for block in GO_IMPORT_BLOCK.findall(code):
        imports += [line.strip() for line in block.splitlines() if line.strip()]
    imports += [spec.strip() for spec in GO_IMPORT_LINE.findall(code)]
    return imports, GO_IMPORT_LINE.sub("", GO_IMPORT_BLOCK.sub("", GO_PACKAGE.sub("", code)))


def with_go_checks(code: str, checks: str) -> str:
    code_imports, code_rest = go_imports(GO_MAIN.sub(r"\1func shownMain()", code))
    check_imports, check_rest = go_imports(checks)
    imports = list(dict.fromkeys(code_imports + check_imports))
    header = "package main\n\n" + ("import (\n" + "".join(f"\t{spec}\n" for spec in imports) + ")\n\n" if imports else "")
    return header + code_rest.strip("\n") + "\n\n" + check_rest.strip("\n") + "\n"


def with_java_checks(code: str, checks: str) -> str:
    imports = list(dict.fromkeys(line.strip() for line in JAVA_IMPORT.findall(code) + JAVA_IMPORT.findall(checks)))
    code_rest = JAVA_PUBLIC_TYPE.sub(r"\1", JAVA_IMPORT.sub("", JAVA_PACKAGE.sub("", code)))
    check_rest = JAVA_IMPORT.sub("", JAVA_PACKAGE.sub("", checks))
    check_rest = JAVA_PUBLIC_TYPE.sub(r"\1", check_rest)
    check_rest = JAVA_CHECKS_CLASS.sub(rf"\1public class {JAVA_CHECKS_NAME}", check_rest, count=1)
    return "".join(f"{line}\n" for line in imports) + "\n" + code_rest.strip("\n") + "\n\n" + check_rest.strip("\n") + "\n"


MAIN_REPLACED = frozenset({"c", "cpp", "rust", "go", "java"})


def with_checks(language: str, code: str, checks: str) -> str:
    if language == "c":
        return with_c_check_macros(code, checks)
    if language == "cpp":
        return with_c_checks(code, checks)
    if language == "rust":
        return with_rust_checks(code, checks)
    if language == "go":
        return with_go_checks(code, checks)
    if language == "java":
        return with_java_checks(code, checks)
    return code.rstrip("\n") + "\n\n" + checks


def plain(text: str) -> str:
    return SPACES.sub(" ", text.strip().strip(QUOTES).strip()).lower()


def call_form(text: str) -> str:
    without_quotes = re.sub(rf"[{QUOTES}]", "", text)
    return SPACE_BY_PUNCTUATION.sub(r"\1", SPACES.sub(" ", without_quotes)).strip().lower()


def bounded(text: str) -> re.Pattern:
    before = r"(?<![\w.])" if text[:1].isalnum() or text[:1] == "_" else ""
    after = r"(?!\w|\.\w)" if text[-1:].isalnum() or text[-1:] == "_" else ""
    return re.compile(before + re.escape(text) + after)


def where_value_is_said(value: str, question: str) -> list[tuple[int, int]]:
    if NUMBER.match(value):
        return [found.span() for found in ANY_NUMBER.finditer(question) if float(found.group()) == float(value)]
    return [found.span() for found in bounded(value).finditer(question)]


def bracket_depths(text: str) -> list[int]:
    depths, depth = [], 0
    for character in text:
        depths.append(depth)
        depth += (character == "(") - (character == ")")
    return depths


def without_thousands_separators(text: str) -> str:
    depths = bracket_depths(text)
    return THOUSANDS.sub(lambda found: found.group().replace(",", "") if depths[found.start()] <= 0 else found.group(), text)


def comparable_call(text: str) -> str:
    return KEYWORD_ARGUMENT.sub("", QUALIFIER.sub("", call_form(text)))


def with_brackets_in_strings_masked(text: str) -> str:
    return STRING_ARGUMENT.sub(lambda found: found.group(1) + found.group(2) + found.group(3).translate(BRACKETS_IN_A_STRING)
                               + found.group(2), text)


def comparable_question(text: str) -> str:
    kept_apart = SPACE_BEFORE_A_VALUE_IN_BRACKETS.sub(KEPT_SPACE, with_brackets_in_strings_masked(text))
    return without_thousands_separators(comparable_call(kept_apart)).replace(KEPT_SPACE, " ")


def closing_after(text: str, start: int) -> int:
    depth = 1
    for index in range(start, len(text)):
        depth += (text[index] == "(") - (text[index] == ")")
        if depth == 0:
            return index + 1
    return len(text)


def call_spans(text: str) -> list[tuple[int, int]]:
    spans, covered_until = [], 0
    for found in CALL_START.finditer(text):
        if found.start() < covered_until:
            continue
        covered_until = closing_after(text, found.end())
        spans.append((found.start(), covered_until))
    return spans


def merged(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    joined: list[tuple[int, int]] = []
    for start, end in spans:
        if joined and start < joined[-1][1]:
            joined[-1] = (joined[-1][0], max(end, joined[-1][1]))
        else:
            joined.append((start, end))
    return joined


@dataclass
class QuestionCalls:
    question: str
    said: set[tuple[int, int]]
    by_start: list[tuple[int, int]]
    starts: list[int]
    by_end: list[tuple[int, int]]
    ends: list[int]
    covered: list[tuple[int, int]]
    covered_starts: list[int]

    @classmethod
    def read(cls, question: str, said: list[tuple[int, int]]) -> "QuestionCalls":
        by_start = sorted(set(call_spans(question)) | set(said))
        by_end = sorted(by_start, key=lambda span: span[1])
        covered = merged(by_start)
        return cls(question, set(said), by_start, [start for start, _ in by_start], by_end, [end for _, end in by_end],
                   covered, [start for start, _ in covered])

    def is_inside_a_call(self, position: int) -> bool:
        index = bisect.bisect_right(self.covered_starts, position) - 1
        return index >= 0 and position < self.covered[index][1]

    def call_before(self, position: int) -> tuple[int, int] | None:
        index = bisect.bisect_right(self.ends, position) - 1
        return self.by_end[index] if index >= 0 else None

    def call_after(self, position: int) -> tuple[int, int] | None:
        index = bisect.bisect_left(self.starts, position)
        return self.by_start[index] if index < len(self.by_start) else None

    def joins(self, start: int, end: int) -> bool:
        return 0 <= end - start <= STATED_WINDOW_CHARS and not CLAUSE_BREAK.search(self.question[start:end])

    def leads_the_call_after(self, value_end: int) -> bool:
        after = self.call_after(value_end)
        return bool(after) and self.joins(value_end, after[0])

    def answers_the_call_before(self, value_start: int, value_end: int) -> bool:
        before = self.call_before(value_start)
        if not before or before not in self.said or not 0 <= value_start - before[1] <= STATED_WINDOW_CHARS:
            return False
        if self.has_its_own_value(before) and self.own_value_start(before) != value_start:
            return False
        return self.joins(before[1], value_start) or not self.leads_the_call_after(value_end)

    def belongs_to_the_call_before(self, value_start: int) -> bool:
        before = self.call_before(value_start)
        return bool(before) and self.joins(before[1], value_start)

    def own_value_start(self, call: tuple[int, int]) -> int | None:
        found = OWN_VALUE_AFTER_A_CALL.match(self.question, call[1])
        return found.end() if found else None

    def has_its_own_value(self, call: tuple[int, int]) -> bool:
        return self.own_value_start(call) is not None

    def answers_the_call_after(self, value_start: int, value_end: int) -> bool:
        if self.belongs_to_the_call_before(value_start):
            return False
        after = self.call_after(value_end)
        return bool(after) and after in self.said and self.joins(value_end, after[0]) and not self.has_its_own_value(after)

    def states(self, value_start: int, value_end: int) -> bool:
        if self.is_inside_a_call(value_start):
            return False
        return self.answers_the_call_before(value_start, value_end) or self.answers_the_call_after(value_start, value_end)


def call_and_value(failure: str, expected: str) -> tuple[str, str]:
    called = CALL_BEFORE_GOT.match(failure.strip())
    if not called:
        return "", expected
    call = called.group(1)
    compared = COMPARED_CALL.match(call)
    if compared and expected == "true":
        return compared.group(1), plain(compared.group(2))
    return call, expected


def is_stated_in(failure: str, question: str) -> bool:
    failure = with_brackets_in_strings_masked(failure)
    wanted = WANTED.search(failure)
    expected = plain(wanted.group(1)) if wanted else ""
    if not expected:
        return False
    call, value = call_and_value(failure, expected.rstrip(".") if NUMBER.match(expected.rstrip(".")) else expected)
    call, value, question = comparable_call(call), call_form(value), comparable_question(question)
    if not call or not value:
        return False
    said = [found.span() for found in bounded(call).finditer(question)]
    if not said:
        return False
    calls = QuestionCalls.read(question, said)
    return any(calls.states(start, end) for start, end in where_value_is_said(value, question))


def crash_counts(language: str, problem: str) -> bool:
    return language in CRASH_COUNTS and bool(MEMORY_ERROR.search(problem))


def outcome_of(verdict: dict | None, language: str, question: str) -> Outcome | None:
    if not verdict:
        return None
    if verdict.get("status") == "failed":
        problem = str(verdict.get("problem") or "").strip()
        if verdict.get("step") not in RUN_STEPS or not crash_counts(language, problem):
            return None
        return Outcome(crash=problem)
    lines = CHECK_LINE.findall(str(verdict.get("stdout") or ""))
    if verdict.get("status") != "passed" or not lines:
        return None
    failed = [detail.strip() for kind, detail in lines if kind == "FAIL"]
    stated = [failure[:FAILURE_CHARS] for failure in failed if is_stated_in(failure, question)]
    return Outcome(passed=sum(kind == "PASS" for kind, _ in lines), failures=stated, unsure=len(failed) - len(stated))


def run_checks(unit: code_sandbox.Unit, checks: str, question: str, seconds: float) -> Outcome | None:
    if seconds < MIN_SECONDS_LEFT:
        return None
    combined = code_sandbox.Unit(unit.language, with_checks(unit.language, unit.code, checks), unit.version,
                                 f"{unit.label} with checks", input_excuse=unit.language not in MAIN_REPLACED)
    verdict = code_sandbox.checked_units([combined], min(code_sandbox.CHECK_ROUND_SECONDS, seconds))[0]
    outcome = outcome_of(verdict, unit.language, question)
    log.info("checks from the question's examples for the %s: %s", unit.label, described_outcome(outcome, verdict))
    return outcome


def described_outcome(outcome: Outcome | None, verdict: dict | None) -> str:
    if outcome is None:
        step = (verdict or {}).get("step") or (verdict or {}).get("status") or "no verdict"
        return f"not usable ({step})"
    if outcome.crash:
        return "crashed"
    return f"{outcome.passed} passed, {len(outcome.failures)} failed, {outcome.unsure} failed on results the question doesn't state"


def problem_text(outcome: Outcome) -> str:
    if outcome.crash:
        return ("With the checks it crashed:\n```\n" + code_sandbox.problem_text({"problem": outcome.crash},
                                                                                    code_sandbox.PROBLEM_CHARS) + "\n```")
    shown = "\n".join(f"CHECK FAIL: {failure}" for failure in outcome.failures[:SHOWN_FAILURES])
    return f"These checks failed:\n```\n{shown}\n```"


def clipped_checks(checks: str) -> str:
    checks = checks.strip("\n")
    return checks if len(checks) <= SHOWN_CHECKS_CHARS else checks[:SHOWN_CHECKS_CHARS].rstrip() + "\n..."


def rewrite_note(unit: code_sandbox.Unit, checks: str, outcome: Outcome) -> str:
    return REWRITE_NOTE.format(problem=problem_text(outcome), fence=FENCE_TAGS[unit.language], checks=clipped_checks(checks),
                               fresh=FRESH_ANSWER)


def checks_source(question: str) -> str:
    return "from the question's examples" if STATED_EXAMPLE.search(question or "") else "for it"


def summary_part(outcome: Outcome | None, question: str = "") -> str:
    if outcome is None or (outcome.unsure and not outcome.is_failing):
        return ""
    source = checks_source(question)
    if outcome.crash:
        return f"it crashed on a check I wrote {source}: {code_sandbox.first_line(outcome.crash)}"
    total = outcome.passed + len(outcome.failures) + outcome.unsure
    if not outcome.failures:
        return f"the {total} checks I wrote {source} pass" if total != 1 else f"the check I wrote {source} passes"
    return (f"{len(outcome.failures)} of the {total} checks I wrote {source} fail "
            f"({outcome.failures[0][:code_sandbox.SUMMARY_PROBLEM_CHARS]})")

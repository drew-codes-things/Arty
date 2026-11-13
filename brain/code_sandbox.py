import json
import logging
import re
import textwrap
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass

import settings
import shown_output
from fresh_answer import FRESH_ANSWER

log = logging.getLogger("brain.code_sandbox")

MAX_REWRITES = 2
CHECK_ROUND_SECONDS = 70
TURN_SECONDS = 160
PARALLEL_CHECKS = 3
MAX_UNITS = 6
TRIVIAL_CHARS = 80
PROBLEM_CHARS = 1200
SUMMARY_PROBLEM_CHARS = 160
TOOL_NAME = "code_sandbox"
NEWEST_PYTHON = "3.14"
VERDICTS = frozenset({"passed", "failed", "unchecked"})
OUTPUT_NOT_COMPARED = frozenset({"sql"})
INLINE_SHELL_CLAIM = re.compile(r"`([^`\n]+)`[^`\n]{0,12}?\b(?:will |would |should )?(?:outputs?|prints?|gives?|returns?|"
                                r"produces?|displays?|echoes)\s*:?\s*`([^`\n]+)`", re.I)
INLINE_TAG = "inline"
SUMMARY_START = "Sandbox check: "
SUMMARY_LINE = re.compile(r"\s*^Sandbox check: [^\n]*\s*\Z", re.M)
CORRECTED_OUTPUT = "the output shown is what it really printed in the sandbox, not what I first wrote"

FENCE = re.compile(r"^([ \t]*)(`{3,}|~{3,})[ \t]*([^\s`{]*)[^\n]*\n(.*?)^[ \t]*\2[ \t]*$", re.M | re.S)
REPL_LINE = re.compile(r"^(?:>>>|\.\.\.)(?: |$)")
LANGUAGES = {
    "python": ("python", "py", "python3", "py3", "pycon"), "javascript": ("javascript", "js", "node", "nodejs", "mjs", "cjs", "jsx"),
    "typescript": ("typescript", "ts", "tsx", "mts"), "bash": ("bash", "sh", "shell", "shell-script"),
    "c": ("c", "h"), "cpp": ("cpp", "c++", "cc", "cxx", "hpp"), "rust": ("rust", "rs"), "go": ("go", "golang"),
    "java": ("java",), "kotlin": ("kotlin", "kt", "kts"), "csharp": ("csharp", "cs", "c#"), "php": ("php",),
    "ruby": ("ruby", "rb"), "lua": ("lua",), "powershell": ("powershell", "ps1", "pwsh", "ps"),
    "sql": ("sql", "sqlite", "sqlite3"), "html": ("html", "htm", "xhtml"), "css": ("css",), "json": ("json",),
    "yaml": ("yaml", "yml"), "toml": ("toml",), "xml": ("xml",),
}
SQL_DIALECTS = {"postgresql": "PostgreSQL", "postgres": "PostgreSQL", "psql": "PostgreSQL", "pgsql": "PostgreSQL",
                "mysql": "MySQL", "mariadb": "MariaDB", "tsql": "SQL Server", "t-sql": "SQL Server", "mssql": "SQL Server",
                "plsql": "Oracle", "oracle": "Oracle"}
ALIASES = {alias: language for language, aliases in LANGUAGES.items() for alias in aliases}
ALIASES.update({dialect: "sql" for dialect in SQL_DIALECTS})
LABELS = {"python": "Python", "javascript": "JavaScript", "typescript": "TypeScript", "bash": "shell script", "c": "C",
          "cpp": "C++", "rust": "Rust", "go": "Go", "java": "Java", "kotlin": "Kotlin", "csharp": "C#", "php": "PHP",
          "ruby": "Ruby", "lua": "Lua", "powershell": "PowerShell", "sql": "SQL", "html": "HTML", "css": "CSS",
          "json": "JSON", "yaml": "YAML", "toml": "TOML", "xml": "XML"}
SHEBANGS = (("python", re.compile(r"^#!.*\bpython")), ("bash", re.compile(r"^#!.*\b(?:ba)?sh\b")),
            ("javascript", re.compile(r"^#!.*\bnode\b")))
GUESSES = (("php", re.compile(r"^\s*<\?php")), ("html", re.compile(r"^\s*<!DOCTYPE html|^\s*<html[\s>]", re.I)),
           ("go", re.compile(r"^package \w+\s*$(?s:.*)^func ", re.M)),
           ("cpp", re.compile(r"^#include\s*<(?:iostream|vector|string|map|memory|algorithm)>", re.M)),
           ("c", re.compile(r"^#include\s*<std(?:io|lib|int|bool)\.h>", re.M)))
SCRIPT_LANGUAGES = frozenset({"python", "javascript", "typescript", "bash", "php", "ruby", "lua", "sql", "powershell"})
ENTRY_POINT = {"c": re.compile(r"\bint\s+main\s*\("), "cpp": re.compile(r"\bint\s+main\s*\("),
               "rust": re.compile(r"\bfn\s+main\s*\("), "go": re.compile(r"^\s*func\s+main\s*\(", re.M),
               "kotlin": re.compile(r"^\s*fun\s+main\s*\(", re.M), "csharp": re.compile(r"\bstatic\s+\w+\s+Main\s*\(")}
JS_TOP_DECLARATION = re.compile(r"^(?:export\s+)?(?:const|let|class|function\*?|async\s+function)\s+(\w+)", re.M)
COMMAND_LINE = re.compile(
    r"^\s*(?:\$\s|#|sudo\s|pip3?\s|pipx\s|uv\s|python3?\s|py\s|npm\s|npx\s|yarn\s|pnpm\s|bun\s|deno\s|node\s|apt(?:-get)?\s|"
    r"dnf\s|yum\s|pacman\s|brew\s|choco\s|winget\s|cargo\s|rustup\s|go\s|git\s|cd\s|mkdir\s|curl\s|wget\s|docker\s|"
    r"podman\s|kubectl\s|systemctl\s|chmod\s|chown\s|export\s|source\s|\.\s|java\s|javac\s|gcc\s|g\+\+\s|clang\s|rustc\s|"
    r"dotnet\s|composer\s|gem\s|bundle\s|ls\b|cat\s|cp\s|mv\s|rm\s|touch\s|code\s|make\b|cmake\s|./)")
PYTHON_VERSION = re.compile(r"\bpython\s*v?(3\.\d{1,2})\b", re.I)
NEEDS_NEWEST_PYTHON = re.compile(r"\bstring\.templatelib\b|(?<![\w\"'])t[\"']|\bannotationlib\b|\bcompression\.zstd\b")
ERROR_LINE = re.compile(r"error|exception|panicked|panic:|fail|undefined|not defined|cannot|expected", re.I)
SQL_DIALECT_ASKED = re.compile(r"\b(postgres(?:ql)?|mysql|mariadb|sql server|t-sql|tsql|oracle|pl/sql)\b", re.I)


@dataclass(frozen=True)
class Unit:
    language: str
    code: str
    version: str = ""
    label: str = ""


class RunnerUnavailable(RuntimeError):
    pass


def runner_url() -> str:
    return settings.CODE_RUNNER_URL.rstrip("/")


def is_on() -> bool:
    return bool(runner_url())


@dataclass(frozen=True)
class Block:
    tag: str
    code: str
    indent: str
    start: int
    end: int
    lead: str


@dataclass(frozen=True)
class Mismatch:
    block: Block
    printed: str


def scanned_blocks(answer: str) -> list[Block]:
    blocks, previous_end = [], 0
    for found in FENCE.finditer(answer or ""):
        indent, tag, body = found.group(1), found.group(3).lower(), found.group(4)
        code = "\n".join(line[len(indent):] if line.startswith(indent) else line for line in body.splitlines())
        lead = answer[previous_end:found.start()].strip()
        blocks.append(Block(tag, textwrap.dedent(code).strip("\n") + "\n", indent, found.start(4), found.end(4), lead))
        previous_end = found.end()
    return blocks


def last_line(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def split_claims(blocks: list[Block]) -> tuple[list[Block], list[Block]]:
    code, claims = [], []
    for block in blocks:
        if code and shown_output.is_claimed_output(block.tag, last_line(block.lead), block.code):
            claims.append(block)
        elif language_of(block.tag, block.code):
            code.append(block)
    return code, claims


def output_claims(answer: str) -> list[Block]:
    return split_claims(scanned_blocks(answer))[1]


def inline_shell_claims(answer: str) -> list[Block]:
    code = code_spans(answer)
    claims = []
    for found in INLINE_SHELL_CLAIM.finditer(answer or ""):
        if any(start <= found.start() < end for start, end in code):
            continue
        claims.append(Block(INLINE_TAG, found.group(2) + "\n", "", found.start(2), found.end(2), f"`{found.group(1)}`"))
    return claims


def fenced_blocks(answer: str) -> list[tuple[str, str]]:
    blocks = scanned_blocks(answer)
    claims = {block.start for block in split_claims(blocks)[1]}
    return [(block.tag, block.code) for block in blocks if block.start not in claims]


def guessed_language(code: str) -> str:
    first = code.lstrip().splitlines()[0] if code.strip() else ""
    for language, shebang in SHEBANGS:
        if shebang.match(first):
            return language
    return next((language for language, pattern in GUESSES if pattern.search(code)), "")


def language_of(tag: str, code: str) -> str:
    if tag:
        return ALIASES.get(tag, "")
    return guessed_language(code)


def without_repl_prompts(code: str) -> str:
    lines = code.splitlines()
    if not any(REPL_LINE.match(line) for line in lines):
        return code
    return "\n".join(line[4:] if len(line) > 3 else "" for line in lines if REPL_LINE.match(line)) + "\n"


def is_trivial(language: str, code: str) -> bool:
    lines = [line for line in code.splitlines() if line.strip()]
    if not lines:
        return True
    if language in {"bash", "powershell"} and all(COMMAND_LINE.match(line) for line in lines):
        return True
    return len(lines) == 1 and len(lines[0].strip()) < TRIVIAL_CHARS


def python_version(request: str, code: str) -> str:
    if NEEDS_NEWEST_PYTHON.search(code):
        return NEWEST_PYTHON
    asked = PYTHON_VERSION.search(request or "")
    return NEWEST_PYTHON if asked and asked.group(1) == NEWEST_PYTHON else ""


def sql_dialect(tag: str, request: str) -> str:
    if tag in SQL_DIALECTS:
        return SQL_DIALECTS[tag]
    asked = SQL_DIALECT_ASKED.search(request or "")
    return asked.group(1) if asked else ""


def variant_for(language: str, tag: str, request: str, code: str) -> str:
    if language == "python":
        return python_version(request, code)
    if language == "sql":
        return sql_dialect(tag, request)
    return ""


def redeclares(blocks: list[str]) -> bool:
    seen = set()
    for block in blocks:
        names = set(JS_TOP_DECLARATION.findall(block))
        if names & seen:
            return True
        seen |= names
    return False


def grouped(language: str, blocks: list[str]) -> list[str]:
    if len(blocks) == 1:
        return blocks
    if language in SCRIPT_LANGUAGES:
        if language in {"javascript", "typescript"} and redeclares(blocks):
            return blocks
        return ["\n".join(blocks)]
    entry = ENTRY_POINT.get(language)
    if entry is not None and sum(bool(entry.search(block)) for block in blocks) == 1:
        return ["\n".join(sorted(blocks, key=lambda block: bool(entry.search(block))))]
    return blocks


def units_in(answer: str, request: str = "") -> list[Unit]:
    by_language: dict[str, list[str]] = {}
    tags: dict[str, str] = {}
    for tag, code in fenced_blocks(answer):
        language = language_of(tag, code)
        if not language:
            continue
        code = without_repl_prompts(code) if language == "python" else code
        if is_trivial(language, code):
            continue
        by_language.setdefault(language, []).append(code)
        tags.setdefault(language, tag)
    units = []
    for language, blocks in by_language.items():
        parts = grouped(language, blocks)
        for number, code in enumerate(parts, start=1):
            label = LABELS[language] if len(parts) == 1 else f"{LABELS[language]} (block {number})"
            units.append(Unit(language, code, variant_for(language, tags[language], request, code), label))
    return units[:MAX_UNITS]


def post_check(url: str, unit: Unit, timeout: float) -> dict:
    body = json.dumps({"language": unit.language, "code": unit.code, "version": unit.version}).encode()
    request = urllib.request.Request(f"{url}/check", data=body, method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def checked_unit(unit: Unit, timeout: float) -> dict:
    url = runner_url()
    try:
        verdict = post_check(url, unit, timeout)
    except urllib.error.HTTPError as error:
        detail = error.read(300).decode(errors="replace") if error.fp else ""
        raise RunnerUnavailable(f"the code runner answered {error.code}: {detail.strip()}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise RunnerUnavailable(f"couldn't reach the code runner at {url}: {error}") from error
    except ValueError as error:
        raise RunnerUnavailable(f"the code runner's answer wasn't JSON: {error}") from error
    if not isinstance(verdict, dict) or verdict.get("status") not in VERDICTS:
        raise RunnerUnavailable(f"the code runner gave an answer without a verdict: {str(verdict)[:200]}")
    return verdict


def checked_units(units: list[Unit], seconds: float = CHECK_ROUND_SECONDS) -> list[dict | None]:
    if not units:
        return []
    pool = ThreadPoolExecutor(max_workers=min(PARALLEL_CHECKS, len(units)), thread_name_prefix="code-sandbox")
    try:
        futures = [pool.submit(checked_unit, unit, seconds) for unit in units]
        wait(futures, timeout=seconds)
        verdicts = []
        for unit, future in zip(units, futures):
            if not future.done():
                log.warning("checking the %s took longer than %.0f s; left unchecked", unit.label, seconds)
                verdicts.append(None)
                continue
            try:
                verdicts.append(future.result())
            except RunnerUnavailable as error:
                log.warning("couldn't check the %s: %s", unit.label, error)
                verdicts.append(None)
        return verdicts
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


@dataclass
class Report:
    units: list
    verdicts: list
    seconds: float

    def failures(self) -> list[tuple[Unit, dict]]:
        return [(unit, verdict) for unit, verdict in zip(self.units, self.verdicts)
                if verdict and verdict["status"] == "failed"]

    def ran_anything(self) -> bool:
        return any(verdict and verdict.get("checked") for verdict in self.verdicts)


def report_for(answer: str, request: str, seconds_left: float) -> Report | None:
    units = units_in(answer, request)
    if not units or seconds_left <= 0:
        return None
    started = time.monotonic()
    verdicts = checked_units(units, min(CHECK_ROUND_SECONDS, seconds_left))
    seconds = round(time.monotonic() - started, 1)
    log.info("sandbox checked %s in %.1f s: %s", ", ".join(unit.label for unit in units), seconds,
             ", ".join(verdict["status"] if verdict else "not checked" for verdict in verdicts))
    if not any(verdicts):
        return None
    return Report(units, verdicts, seconds)


def printed_by(verdict: dict | None) -> str | None:
    if not verdict or verdict.get("status") != "passed" or "run" not in (verdict.get("checked") or []):
        return None
    if verdict.get("stdout_cut") or not isinstance(verdict.get("stdout"), str):
        return None
    return verdict["stdout"]


def claim_mismatches(claims: list[Block], printed: str) -> list[Mismatch]:
    if len(claims) != 1:
        return []
    claim = claims[0]
    if shown_output.is_partial(claim.code) or shown_output.is_shown_by(claim.code, printed):
        return []
    return [Mismatch(claim, printed)]


def shell_call_mismatches(unit: Unit, claims: list[Block], seconds: float) -> list[Mismatch]:
    if unit.language != "bash" or not shown_output.is_stable_with_arguments_given(unit.code):
        return []
    runs = [(claim, calls) for claim in claims if (calls := shown_output.shell_calls(claim.lead, unit.code))][:MAX_UNITS]
    if not runs or seconds <= 0:
        return []
    called = [Unit(unit.language, unit.code + "\n".join(calls) + "\n", unit.version, unit.label) for _, calls in runs]
    mismatches = []
    for (claim, _), verdict in zip(runs, checked_units(called, min(CHECK_ROUND_SECONDS, seconds))):
        printed = printed_by(verdict)
        if printed is None or not printed.strip():
            continue
        mismatches += claim_mismatches([claim], printed)
    return mismatches


def output_mismatches(answer: str, report: Report | None, seconds_left: float) -> list[Mismatch]:
    claims = output_claims(answer)
    if report is None or not (claims or inline_shell_claims(answer)):
        return []
    ran = [(unit, printed) for unit, verdict in zip(report.units, report.verdicts) if (printed := printed_by(verdict)) is not None]
    if len(ran) != 1:
        return []
    unit, printed = ran[0]
    if unit.language in OUTPUT_NOT_COMPARED:
        return []
    if printed.strip():
        return claim_mismatches(claims, printed) if shown_output.is_stable(unit.code) else []
    return shell_call_mismatches(unit, claims + inline_shell_claims(answer), seconds_left)


def output_note(mismatches: list[Mismatch]) -> str:
    return shown_output.rewrite_note([(mismatch.block.code, mismatch.printed) for mismatch in mismatches])


def with_real_output(answer: str, mismatches: list[Mismatch]) -> str:
    for mismatch in sorted(mismatches, key=lambda found: found.block.start, reverse=True):
        block = mismatch.block
        answer = answer[:block.start] + replacement(mismatch) + answer[block.end:]
    return answer


def replacement(mismatch: Mismatch) -> str:
    block = mismatch.block
    if block.tag != INLINE_TAG:
        return shown_output.shown_block(mismatch.printed, block.indent)
    lines = shown_output.normalized_lines(mismatch.printed)
    return lines[0] if len(lines) == 1 else "; ".join(lines)


def problem_text(verdict: dict, limit: int) -> str:
    problem = str(verdict.get("problem") or "").strip()
    return problem if len(problem) <= limit else problem[:limit].rstrip() + "\n..."


def rewrite_note(failures: list[tuple[Unit, dict]]) -> str:
    parts = []
    for unit, verdict in failures:
        tool = verdict.get("tool") or unit.label
        parts.append(f"The {unit.label} failed at the {verdict.get('step', 'check')} step ({tool}):\n```\n"
                     f"{problem_text(verdict, PROBLEM_CHARS)}\n```")
    return ("(I ran the code from your answer in a sandbox and it doesn't work yet.\n\n" + "\n\n".join(parts)
            + "\n\nFix what these errors show and write your whole answer again. Change only what the errors need, keep "
              "using what the question asked for, and don't mention this check. " + FRESH_ANSWER + ")")


def first_line(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip() and line.strip() != "..."]
    if not lines:
        return ""
    pool = [line for line in lines if ERROR_LINE.search(line)] or lines
    chosen = pool[-1] if lines[0].startswith("Traceback") else pool[0]
    return chosen if len(chosen) <= SUMMARY_PROBLEM_CHARS else chosen[:SUMMARY_PROBLEM_CHARS].rstrip() + "..."


def described(unit: Unit, verdict: dict | None) -> str:
    if verdict is None:
        return f"the {unit.label} couldn't be checked"
    notes = "; ".join(str(note) for note in verdict.get("notes") or [] if note)
    tool = verdict.get("tool") or unit.label
    if verdict["status"] == "failed":
        return f"the {unit.label} still fails ({tool}): {first_line(verdict.get('problem'))}"
    if verdict["status"] == "unchecked":
        return f"the {unit.label} couldn't be checked ({notes or 'no checker for it'})"
    checked = verdict.get("checked") or []
    if "tests" in checked:
        text = f"the {unit.label}'s tests pass ({tool})"
    elif "run" in checked:
        text = f"the {unit.label} ran without errors ({tool})"
    elif "compile" in checked:
        text = f"the {unit.label} compiles ({tool})"
    else:
        text = f"the {unit.label} has no syntax errors ({tool})"
    return f"{text} ({notes})" if notes else text


def without_summary(answer: str) -> tuple[str, str]:
    found = SUMMARY_LINE.search(answer or "")
    if not found:
        return answer, ""
    return answer[:found.start()], found.group(0).strip()


def code_spans(text: str) -> list[tuple[int, int]]:
    return [(found.start(), found.end()) for found in FENCE.finditer(text or "")]


def summary(report: Report | None, corrected_output: bool = False) -> str:
    if report is None:
        return ""
    parts = [described(unit, verdict) for unit, verdict in zip(report.units, report.verdicts)]
    if corrected_output:
        parts.append(CORRECTED_OUTPUT)
    return f"{SUMMARY_START}{'; '.join(parts)}."

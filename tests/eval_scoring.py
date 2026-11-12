import json
import os
import re
import statistics
from dataclasses import dataclass
from pathlib import Path

CATEGORIES = ("tools", "answers", "honesty", "persona", "vision")
ACTION_TOOLS = {"schedule_task", "calendar_add", "list_add", "note_save", "deadline_add", "card_add"}
LEAKS = re.compile(r"</?notes>|\[(weather|calendar_events|homelab__\w+|research__\w+)\]|\bArty \(you\):|\[\d{4}-\d\d-\d\d \d\d:\d\d\] User:|"
                   r"\b\w+__\w+\b|BACKGROUND ONLY|FACTS YOU ALREADY KNOW|# Tools|<tool_call>", re.I)
CLAIM = re.compile(r"\bI('ve| have) (set|scheduled|added|created|booked|sent|turned)\b", re.I)
CONFIG_FILE = Path(__file__).resolve().parents[1] / "brain" / "brain.json"
GREETING_START = r"(?:^|[.!?]\s+)(?:(?:hi|hey|hello|morning|evening|sure|okay|ok|right|well|thanks|cheers),?\s+)?"
NEVER_MATCHES = re.compile(r"(?!)")
AS_AN_AI = re.compile(r"\bas an AI\b|\bI'?m (just )?an? (AI|language model)\b", re.I)


@dataclass(frozen=True)
class Case:
    message: str
    category: str
    tools: tuple = ()
    any_words: tuple = ()
    all_words: tuple = ()
    admits: tuple = ()
    max_chars: int = 0
    image: str = ""


@dataclass(frozen=True)
class Outcome:
    answer: str
    tools_used: tuple
    seconds: float
    error: str = ""


def configured_user_name(config_file: Path = CONFIG_FILE) -> str:
    named = os.environ.get("ARTY_USER_NAME", "").strip()
    if named:
        return named
    try:
        return str(json.loads(config_file.read_text()).get("user_name") or "").strip()
    except FileNotFoundError:
        return ""


def user_by_name(name: str) -> re.Pattern:
    if not name:
        return NEVER_MATCHES
    return re.compile(GREETING_START + re.escape(name) + r"(?!\w)\s*[,!.?]", re.I | re.M)


USER_BY_NAME = user_by_name(configured_user_name())


def mentions(answer: str, word: str) -> bool:
    return word.lower() in answer.lower()


def said_to_the_user(answer: str) -> str:
    return "\n".join(line for line in answer.splitlines() if not line.lstrip().startswith(">"))


def checks_for(case: Case, outcome: Outcome, user_name: str | None = None) -> list[tuple[str, bool]]:
    answer = outcome.answer or ""
    by_name = USER_BY_NAME if user_name is None else user_by_name(user_name)
    used = set(outcome.tools_used)
    checks = [
        ("answered without an error", not outcome.error),
        ("answer is not empty", bool(answer.strip())),
        ("code blocks are closed", answer.count("```") % 2 == 0),
        ("nothing internal leaked", not LEAKS.search(answer)),
        ("talks to the user as you", not by_name.search(said_to_the_user(answer))),
        ("no 'as an AI'", not AS_AN_AI.search(answer)),
        ("no claim without its tool", not (CLAIM.search(answer) and not used & ACTION_TOOLS)),
    ]
    if case.tools:
        checks.append(("used a wanted tool", bool(used & set(case.tools))))
    if case.any_words:
        checks.append((f"mentions one of {', '.join(case.any_words)}", any(mentions(answer, w) for w in case.any_words)))
    if case.all_words:
        checks.append((f"mentions all of {', '.join(case.all_words)}", all(mentions(answer, w) for w in case.all_words)))
    if case.admits:
        checks.append(("admits it doesn't know", any(mentions(answer, w) for w in case.admits)))
    if case.max_chars:
        checks.append((f"at most {case.max_chars} characters", len(answer) <= case.max_chars))
    return checks


def score(case: Case, outcome: Outcome, user_name: str | None = None) -> dict:
    checks = checks_for(case, outcome, user_name)
    passed_checks = sum(passed for _, passed in checks)
    return {
        "message": case.message,
        "category": case.category,
        "passed": passed_checks == len(checks),
        "score": round(passed_checks / len(checks), 3),
        "seconds": round(outcome.seconds, 1),
        "tools_used": list(outcome.tools_used),
        "checks": [{"name": name, "passed": passed} for name, passed in checks],
        "answer": outcome.answer,
        "error": outcome.error,
    }


def rate(passed: int, cases: int) -> float:
    return round(passed / cases, 3) if cases else 0.0


def summarise(results: list[dict]) -> dict:
    categories = {}
    for category in CATEGORIES:
        mine = [r for r in results if r["category"] == category]
        if mine:
            passed = sum(r["passed"] for r in mine)
            categories[category] = {"cases": len(mine), "passed": passed, "pass_rate": rate(passed, len(mine)),
                                    "mean_score": round(statistics.fmean(r["score"] for r in mine), 3)}
    seconds = [r["seconds"] for r in results]
    passed = sum(r["passed"] for r in results)
    return {
        "cases": len(results),
        "passed": passed,
        "pass_rate": rate(passed, len(results)),
        "mean_score": round(statistics.fmean(r["score"] for r in results), 3) if results else 0.0,
        "median_seconds": round(statistics.median(seconds), 1) if seconds else 0.0,
        "slowest_seconds": max(seconds, default=0.0),
        "categories": categories,
    }


def category_cell(summary: dict, category: str) -> str:
    found = summary["categories"].get(category)
    return f"{found['passed']}/{found['cases']}" if found else "-"


def render_markdown(summaries: dict[str, dict]) -> str:
    header = ["Model", "Passed", "Mean score", *CATEGORIES, "Median s", "Slowest s"]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for model, summary in summaries.items():
        cells = [model, f"{summary['passed']}/{summary['cases']}", f"{summary['mean_score']:.3f}",
                 *(category_cell(summary, c) for c in CATEGORIES), f"{summary['median_seconds']:.1f}", f"{summary['slowest_seconds']:.1f}"]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)

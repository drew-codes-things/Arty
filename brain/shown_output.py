import re

import coding
from fresh_answer import FRESH_ANSWER

OUTPUT_TAGS = frozenset({"", "text", "txt", "output", "console", "plaintext", "plain", "stdout", "out", "terminal"})
DATA_TAGS = frozenset({"html", "htm", "xml", "json", "yaml", "yml", "csv", "tsv", "markdown", "md"})
OUTPUT_LEAD = re.compile(r"\b(?:outputs?|outputted|prints?|printed|printing|displays?|displayed|you(?:'ll| will| should)? see|"
                         r"shows? (?:up )?(?:in|on) the (?:console|terminal|screen))\b[^\n]*:$", re.I)
LEAD_MARKUP = re.compile(r"[*_#>]+")
MIN_CODE_LINES = 1
PROMPT_LINE = re.compile(r"^\s*(?:\$|>|PS[^>\n]*>|[A-Z]:\\[^>\n]*>)\s")
ELIDED_LINE = re.compile(r"^\s*(?:\.\.\.|…|\[\.\.\.\])\s*$", re.M)
UNSTABLE = re.compile(
    r"\brandom|\brandint\b|\brand\s*\(|\bsrand\b|\$RANDOM|\buuid|\bnow\s*\(|\bnew Date\b|\btime\s*\(|\btime\.|\bdatetime\b|"
    r"\bdate\b|\bInstant\b|\bLocalDate|\bSystemTime\b|\bchrono\b|\bclock\s*\(|perf_counter|nanoTime|currentTimeMillis|getpid|"
    r"\bhash\s*\(|hashCode|HashMap|HashSet|\bmap\[|\bset\s*\(|\bfrozenset\b|\bid\s*\(|thread|goroutine|\bgo\s+func\b|"
    r"\bgo\s+\w+\(|\basync\b|\bawait\b|\bPromise\b|setTimeout|setInterval|os\.environ|process\.env|getenv|\benv::|\$HOME|"
    r"\$USER|\$PWD|whoami|hostname|platform\.|sys\.version|getcwd|\bpwd\b|\bls\b|\bfind\s|listdir|readdir|\bglob\b|"
    r"\binput\s*\(|stdin|argv|\$\{?[1-9@*#]|\bScanner\b|\bread\s+-|\bread\s+\w", re.I)
SHELL_ARGUMENTS = re.compile(r"\$\{?[1-9@*#]\}?")
INLINE_CODE = re.compile(r"`([^`\n]+)`")
SHELL_FUNCTION = re.compile(r"^\s*(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\s*\)|^\s*function\s+([A-Za-z_][\w-]*)\b", re.M)
ARGUMENTS_JUST_BEFORE = re.compile(r"\b(?:arguments?|args?|parameters?|inputs?)\s*$", re.I)
MAX_SHOWN_CHARS = 1500


def is_output_lead(lead: str) -> bool:
    return bool(OUTPUT_LEAD.search(LEAD_MARKUP.sub("", lead).strip()))


def looks_like_code(text: str) -> bool:
    return len(coding.CODE_LINE.findall(text)) >= MIN_CODE_LINES


def is_claimed_output(tag: str, lead: str, text: str) -> bool:
    if tag not in OUTPUT_TAGS and tag not in DATA_TAGS:
        return False
    return is_output_lead(lead) and bool(text.strip()) and not looks_like_code(text)


def normalized_lines(text: str) -> list[str]:
    lines = [line.rstrip() for line in str(text or "").replace("\r\n", "\n").split("\n")]
    while lines and (not lines[0] or PROMPT_LINE.match(lines[0])):
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def is_partial(text: str) -> bool:
    return bool(ELIDED_LINE.search(text))


def is_shown_by(claimed: str, printed: str) -> bool:
    wanted, got = normalized_lines(claimed), normalized_lines(printed)
    if not wanted:
        return True
    size = len(wanted)
    return any(got[start:start + size] == wanted for start in range(len(got) - size + 1))


def is_stable(code: str) -> bool:
    return not UNSTABLE.search(code)


def is_stable_with_arguments_given(code: str) -> bool:
    return is_stable(SHELL_ARGUMENTS.sub("", code))


def shell_functions(code: str) -> list[str]:
    return list(dict.fromkeys(first or second for first, second in SHELL_FUNCTION.findall(code)))


def shell_calls(lead: str, code: str) -> list[str]:
    functions = shell_functions(code)
    if not functions:
        return []
    calls = []
    for found in INLINE_CODE.finditer(lead):
        span = found.group(1).strip()
        words = span.split()
        if words and words[0] in functions:
            calls.append(span)
        elif len(functions) == 1 and ARGUMENTS_JUST_BEFORE.search(lead[:found.start()]):
            calls.append(f"{functions[0]} {span}")
    return calls


def clipped(text: str) -> str:
    text = text.strip("\n")
    return text if len(text) <= MAX_SHOWN_CHARS else text[:MAX_SHOWN_CHARS].rstrip() + "\n..."


def rewrite_note(pairs: list[tuple[str, str]]) -> str:
    parts = [f"Your answer says it prints:\n```\n{clipped(claimed)}\n```\nbut it really printed:\n```\n{clipped(printed)}\n```"
             for claimed, printed in pairs]
    return ("(I ran the code from your answer in a sandbox and the output it shows isn't what the code prints.\n\n"
            + "\n\n".join(parts)
            + "\n\nIf the code doesn't do what the question asks, fix the code. If the code is right, show the output it really "
              "prints. Write your whole answer again and don't mention this check. " + FRESH_ANSWER + ")")


def shown_block(printed: str, indent: str) -> str:
    return "".join(f"{indent}{line}\n" if line else "\n" for line in normalized_lines(printed))

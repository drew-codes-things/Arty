import re
import shlex
from dataclasses import dataclass

INPUT_LABEL = r"(?:sample |example )?input(?: ?#?\d+)?"
LABEL_ALONE = re.compile(rf"^[ \t]*[*_]*{INPUT_LABEL}[*_]*[ \t]*:?[ \t]*[*_]*[ \t]*$", re.I | re.M)
LABEL_WITH_VALUE = re.compile(rf"(?:^|(?<=[\s(]))[*_]*{INPUT_LABEL}[*_]*[ \t]*:[*_]*[ \t]*(\S[^\n]*)$", re.I | re.M)
OUTPUT_LABEL = re.compile(r"^[ \t]*[*_]*(?:sample |example |expected )?output\b", re.I)
OUTPUT_LABEL_INLINE = re.compile(r"[\s,;]+[*_]*(?:sample |example |expected )?output[*_]*\s*:.*$", re.I)
FENCED_START = re.compile(r"^[ \t]*```[^\n]*$")
FENCE = "```"
INLINE_CODE = re.compile(r"`([^`\n]+)`")
FENCED_BODY = re.compile(r"```[^\n]*\n(.*?)```", re.S)
PROMPT = re.compile(r"^\$\s+")
PLAIN_DATA = re.compile(r"^[\d\s.,;:+\-\[\]()]+$")
MAX_ARGUMENTS = 20
LEAD_IN_CHARS = 60
LEAD_IN = re.compile(r"(?:usage|run (?:it |this |the \w+ )?(?:as|with|like|using)|call(?:ed)? (?:it )?(?:as|like|with)|"
                     r"works? like|invoked? (?:as|like|with)|e\.g\.|for example|example|such as|like this|"
                     r"should (?:be run|work) (?:as|like)|test (?:it )?with|^\s*(?:run|try)|[.!:]\s*(?:run|try))\s*[:,]?\s*$", re.I)
INTERPRETERS = frozenset({"python", "python3", "node", "bash", "sh", "ruby", "php"})
SCRIPT = re.compile(r"^[\w./-]+\.(?:py|js|sh|rb|php|go)$")
COMPILED_PROGRAM = re.compile(r"^\./[\w.-]+$")
ECHO_OPTIONS = frozenset({"-n", "-e", "-E", "-ne", "-en"})
ECHO_ESCAPES_ON = frozenset({"-e", "-ne", "-en"})
PLACEHOLDER = re.compile(r"^(?:[A-Z]|<[^<>]+>)$")
SUFFIX_LANGUAGES = {".py": "python", ".js": "javascript", ".sh": "bash", ".rb": "ruby", ".php": "php", ".go": "go"}
LANGUAGE_NAMES = {"python": "python", "python3": "python", "py": "python", "javascript": "javascript", "js": "javascript",
                  "node": "javascript", "bash": "bash", "sh": "bash", "shell": "bash", "ruby": "ruby", "php": "php", "go": "go"}
ANSWER_LANGUAGE = re.compile(r"```[ \t]*(\w+)")
NAMED_LANGUAGE = re.compile(r"\b(" + "|".join(LANGUAGE_NAMES) + r")\b", re.I)
WRITE_REQUEST = re.compile(r"\b(?:write|create|make|build|code|generate|give me|show me)\b[^.?!\n]{0,60}?"
                           r"\b(?:script|program|function|app|tool|code|cli|one-liner)\b", re.I)
SHOWS_A_RESULT = re.compile(r"[`\s]*(?:(?:should|would|will|must|ought to|is expected to)\s+(?:then\s+)?)?"
                            r"(?:print|prints|printed|output|outputs|give|gives|return|returns|show|shows|display|displays|"
                            r"produce|produces|yield|yields|echo|echoes)\b|[`\s]*(?:->|=>|→)", re.I)
HERE_STRING = "<<<"
FROM_A_FILE = "<"
PIPE = "|"
SHELL_OPERATORS = frozenset({"|", "||", "&", "&&", ";", ">", ">>", "<", "<<", "<<<", "&>"})
ESCAPED_LINE_BREAK = "\\n"
PRINTF_SPECIFIER = re.compile(r"%[-+ 0#]*\d*(?:\.\d+)?[sdifgu]|%%")
PRINTF_ESCAPES = {"\\n": "\n", "\\t": "\t"}
MAX_PRINTF_ROUNDS = 100


@dataclass(frozen=True)
class StatedInput:
    stdin: str = ""
    arguments: tuple = ()


@dataclass(frozen=True)
class CommandLine:
    text: str
    start: int


def data_value(value: str, opened_with_a_bracket: bool) -> str:
    value = OUTPUT_LABEL_INLINE.sub("", value).strip().rstrip(".")
    if opened_with_a_bracket and ")" in value:
        value = value[:value.index(")")].rstrip()
    inline = INLINE_CODE.fullmatch(value)
    if inline:
        return inline.group(1)
    return value if PLAIN_DATA.match(value) else ""


def block_after(text: str, end: int) -> str:
    lines = text[end:].lstrip("\n").splitlines()
    if lines and FENCED_START.match(lines[0]):
        body = []
        for line in lines[1:]:
            if line.strip().startswith(FENCE):
                return "\n".join(body)
            body.append(line)
        return ""
    body = []
    for line in lines:
        if not line.strip() or OUTPUT_LABEL.match(line):
            break
        body.append(line)
    return "\n".join(body) if all(PLAIN_DATA.match(line) for line in body) else ""


def labelled_input(text: str) -> str:
    alone, inline = LABEL_ALONE.search(text), LABEL_WITH_VALUE.search(text)
    if alone and (not inline or alone.start() < inline.start()):
        return block_after(text, alone.end())
    if not inline:
        return ""
    opened = inline.start() > 0 and text[inline.start() - 1] == "("
    return data_value(inline.group(1), opened)


def command_lines(text: str) -> list[CommandLine]:
    found = [CommandLine(match.group(1), match.start()) for match in INLINE_CODE.finditer(text)]
    for match in FENCED_BODY.finditer(text):
        found += [CommandLine(line, match.start()) for line in match.group(1).splitlines()]
    return [CommandLine(PROMPT.sub("", line.text.strip()), line.start) for line in found]


def shell_words(line: str) -> list[str]:
    lexer = shlex.shlex(line, posix=True, punctuation_chars="|&;<>")
    lexer.whitespace_split = True
    return list(lexer)


def pipeline_segments(words: list[str]) -> list[list[str]]:
    segments = [[]]
    for word in words:
        if word == PIPE:
            segments.append([])
        else:
            segments[-1].append(word)
    return segments


def program_and_rest(words: list[str]) -> tuple[str, list[str]] | None:
    if len(words) >= 2 and words[0] in INTERPRETERS and SCRIPT.match(words[1]):
        return words[1], words[2:]
    if words and COMPILED_PROGRAM.match(words[0]):
        return words[0], words[1:]
    return None


def echoed_text(words: list[str]) -> str:
    payload = [word for word in words[1:] if word not in ECHO_OPTIONS]
    text = " ".join(payload)
    if ECHO_ESCAPES_ON & set(words[1:]):
        text = text.replace(ESCAPED_LINE_BREAK, "\n")
    return text.rstrip("\n") + "\n"


def printed_text(words: list[str]) -> str:
    if len(words) < 2:
        return ""
    form, values = words[1], list(words[2:])
    for escaped, character in PRINTF_ESCAPES.items():
        form = form.replace(escaped, character)
    rounds, printed = 0, []
    while rounds == 0 or values and rounds < MAX_PRINTF_ROUNDS:
        printed.append(PRINTF_SPECIFIER.sub(lambda found: "%" if found.group() == "%%" else (values.pop(0) if values else ""), form))
        rounds += 1
    return "".join(printed).rstrip("\n") + "\n"


def input_after_the_program(rest: list[str]) -> StatedInput | None:
    arguments = []
    for index, word in enumerate(rest):
        if word == HERE_STRING and index + 1 < len(rest):
            return StatedInput(stdin=rest[index + 1].rstrip("\n") + "\n", arguments=tuple(arguments[:MAX_ARGUMENTS]))
        if word == FROM_A_FILE:
            return None
        if word in SHELL_OPERATORS:
            break
        if PLACEHOLDER.match(word):
            return None
        arguments.append(word)
    return StatedInput(arguments=tuple(arguments[:MAX_ARGUMENTS])) if arguments else None


def stated_by_a_command(words: list[str]) -> tuple[str, StatedInput] | None:
    segments = pipeline_segments(words)
    if len(segments) >= 2 and segments[0] and segments[0][0] in ("echo", "printf"):
        called = program_and_rest(segments[1])
        if not called or len(segments[0]) < 2:
            return None
        text = echoed_text(segments[0]) if segments[0][0] == "echo" else printed_text(segments[0])
        return called[0], StatedInput(stdin=text)
    called = program_and_rest(segments[0])
    if not called:
        return None
    found = input_after_the_program(called[1])
    return (called[0], found) if found else None


def program_language(name: str) -> str:
    return next((language for suffix, language in SUFFIX_LANGUAGES.items() if name.endswith(suffix)), "")


def answer_languages(question: str, answer: str) -> set[str]:
    fenced = {LANGUAGE_NAMES.get(found.lower(), "") for found in ANSWER_LANGUAGE.findall(answer or "")} - {""}
    prose = INLINE_CODE.sub(" ", FENCED_BODY.sub(" ", question))
    return fenced or {LANGUAGE_NAMES[found.lower()] for found in NAMED_LANGUAGE.findall(prose)}


def fits_the_answer(name: str, question: str, answer: str) -> bool:
    language = program_language(name)
    return bool(language) and language in answer_languages(question, answer)


def is_in_another_language(name: str, question: str, answer: str) -> bool:
    language = program_language(name)
    wanted = answer_languages(question, answer)
    return bool(language) and bool(wanted) and language not in wanted


def shows_a_result(command: CommandLine, question: str) -> bool:
    end = question.find(command.text, command.start) + len(command.text)
    return bool(SHOWS_A_RESULT.match(question[end:]))


def is_an_example_of_the_answer(name: str, command: CommandLine, question: str, answer: str) -> bool:
    later = question[command.start:].replace(command.text, " ", 1)
    if is_in_another_language(name, question, answer):
        return False
    if not WRITE_REQUEST.search(later):
        return True
    return fits_the_answer(name, question, answer) and shows_a_result(command, question)


def is_about_the_program(name: str, command: CommandLine, question: str, answer: str) -> bool:
    lead = question[max(0, command.start - LEAD_IN_CHARS):command.start]
    if LEAD_IN.search(lead.rstrip()) and is_an_example_of_the_answer(name, command, question, answer):
        return True
    named = re.compile(rf"(?<![\w./-]){re.escape(name.removeprefix('./'))}(?![\w-])")
    elsewhere = question.replace(command.text, " ")
    return bool(named.search(elsewhere) or named.search(answer or ""))


def stated_input(question: str, answer: str = "") -> StatedInput | None:
    question = question or ""
    labelled = labelled_input(question)
    if labelled.strip():
        return StatedInput(stdin=labelled.rstrip("\n") + "\n")
    for command in command_lines(question):
        try:
            found = stated_by_a_command(shell_words(command.text))
        except ValueError:
            continue
        if found and is_about_the_program(found[0], command, question, answer):
            return found[1]
    return None

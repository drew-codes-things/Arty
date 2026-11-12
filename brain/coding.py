import re

STRICT_LANGUAGES = (r"python3?|javascript|typescript|node\.?js|kotlin|c\+\+|c#|golang|php|bash|zsh|powershell|sql|"
                    r"postgres(?:ql)?|mysql|sqlite|haskell|scala|lua|elixir|html|css|django|flask")
ANY_LANGUAGE = rf"{STRICT_LANGUAGES}|java|rust|ruby|swift(?!\s+codes?\b)|dart|perl|shell|react"
AMBIGUOUS_ARTEFACT = r"code|scripts?|functions?|methods?|class(?:es)?|programs?|quer(?:y|ies)|commands?|components?|loops?|algorithms?"
ARTEFACT_JOB = (r"returns?|takes|accepts|checks|computes|calculates|converts|parses|sorts|validates|reverses|counts|finds|"
                r"generates|prints|renames|deletes|moves|copies|backs? up|downloads|uploads|automates|scrapes|installs|"
                r"monitors|fetches|lists|resizes|compresses|extracts|merges|selects|implements|extends|inherits|represents|"
                r"stores|handles|rename|delete|move|copy|back up|download|upload|automate|scrape|install|monitor|fetch|"
                r"resize|compress|extract|merge|empty|empties")

FENCED_CODE = re.compile(r"```[\w+#.-]*[ \t]*\n")
PROBLEM_REPORT = re.compile(
    r"Traceback \(most recent call last\)"
    r"|^\s*File \"[^\"]+\", line \d+"
    r"|\b[A-Z]\w*(?:Error|Exception): \S"
    r"|\b(?:[A-Z][a-z]+){2,}(?:Error|Exception)\b"
    r"|^\s+at \S+ ?\(.*\.\w+:\d+(?::\d+)?\)"
    r"|\b[\w./\\-]+\.\w+(?::\d+){1,2}:\s*(?:fatal )?error\b"
    r"|\b[\w./\\-]+\.\w+\(\d+,\d+\): error\b"
    r"|\berror(?:\[E\d{4}\]| TS\d{4}| CS\d{4}):"
    r"|\bSegmentation fault\b|\bnpm ERR!|\bundefined reference to\b|\bcannot find symbol\b|\bpanic: runtime error\b",
    re.MULTILINE)
CODE_LINE = re.compile(
    r"^\s*(?:def \w+\(|class \w+[(:]|function \w*\(|return\b|import \w|from [\w.]+ import\b|#include\b|"
    r"(?:public|private|protected) \w|(?:const|let|var) \w+ ?=|(?:if|for|while) ?\(.*\)\s*\{|for \w+ in .+:$|"
    r"\}\s*$|SELECT\b.+\bFROM\b)", re.MULTILINE)
PASTED_CODE_LINES = 2
CODE_NAMED_OUTRIGHT = re.compile(
    rf"\bregex(?:es)?\b|\bregular expressions?\b|\bsql (?:query|queries|statement)\b|"
    rf"\b(?:shell|bash|zsh|terminal|powershell|command[- ]line|linux|cmd) (?:command|script|one-liner)s?\b|"
    rf"\bunit tests?\b|\bsource code\b|\bcode ?base\b|\bcode snippets?\b|\bstack ?traces?\b|\bpull requests?\b|"
    rf"\bmerge conflicts?\b|\bdockerfile\b|\bmakefile\b|\bcron ?jobs?\b|\bapi endpoints?\b|"
    rf"\b(?:{ANY_LANGUAGE}) (?:{AMBIGUOUS_ARTEFACT}|one-liners?|snippets?|homework|assignments?|projects?|errors?|bugs?|modules?)\b|"
    rf"\b(?:{AMBIGUOUS_ARTEFACT}) in (?:{ANY_LANGUAGE})\b",
    re.IGNORECASE)
CODE_JOB = re.compile(rf"\b(?:{AMBIGUOUS_ARTEFACT}) (?:that|which|to) (?:automatically )?(?:{ARTEFACT_JOB})\b", re.IGNORECASE)
CODING_VERB = re.compile(r"\b(?:debug|refactor)\w*\b", re.IGNORECASE)
WORK_ON_CODE = re.compile(
    r"\b(?:fix|debug|review|refactor|explain|optimi[sz]e|improve|clean up|test)\s+(?:my|this|the following|some|your)\s+code\b"
    r"(?!\s+(?:for|to|of|on)\b)|\b(?:fix|review|explain)\s+the\s+code\s*[?.!:]", re.IGNORECASE)
ASKS_ABOUT_A_LANGUAGE = re.compile(
    rf"\b(?:how|what|why|which|explain|show|write|difference|when|can|should)\b[^\n]*?\b(?:in|using|with) (?:{STRICT_LANGUAGES})\b",
    re.IGNORECASE)
ASKS_FOR_WORK = re.compile(
    r"\b(?:write|create|make|build|generate|fix|debug|refactor|review|explain|optimi[sz]e|improve|rewrite|convert|"
    r"simplify|clean up|test|help)\b|\bwhat does\b|\bwhy (?:does|doesn't|won't|isn't|is)\b", re.IGNORECASE)
PROGRAMMING_SIGNAL = re.compile(
    rf"\b(?:{STRICT_LANGUAGES})\b|\b(?:compiler?|compiles?|syntax|variables?|arrays?|recursion|recursive|api|endpoints?|"
    rf"librar(?:y|ies)|frameworks?|repo(?:sitory)?|git(?:hub)?|json|yaml|npm|pip|docker|programming|developer|"
    rf"dataframe|pandas|numpy|async|lambda|boolean|integer|null|undefined|re-?renders?|stdout|stderr)\b|"
    rf"\b\w+\([^()\n]*\)|\b[a-z]+_[a-z_]+\b|`[^`\n]+`|\b\w+\.(?:py|js|ts|tsx|jsx|java|kt|rs|cpp|cs|rb|php|sh|sql|json|ya?ml|toml)\b",
    re.IGNORECASE)
AMBIGUOUS_ARTEFACT_WORD = re.compile(rf"\b(?:{AMBIGUOUS_ARTEFACT})\b", re.IGNORECASE)


def has_pasted_code(text: str) -> bool:
    return bool(FENCED_CODE.search(text)) or len(CODE_LINE.findall(text)) >= PASTED_CODE_LINES


def reports_a_code_problem(text: str) -> bool:
    return bool(PROBLEM_REPORT.search(text))


def asks_for_code_work(text: str) -> bool:
    if CODE_NAMED_OUTRIGHT.search(text) or CODE_JOB.search(text) or WORK_ON_CODE.search(text):
        return True
    if ASKS_ABOUT_A_LANGUAGE.search(text):
        return True
    if not AMBIGUOUS_ARTEFACT_WORD.search(text):
        return False
    return bool(CODING_VERB.search(text)) or (bool(ASKS_FOR_WORK.search(text)) and bool(PROGRAMMING_SIGNAL.search(text)))


def is_request(text: str | None) -> bool:
    text = text or ""
    return has_pasted_code(text) or reports_a_code_problem(text) or asks_for_code_work(text)

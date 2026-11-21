import re

NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
NUMBER_WORDS = re.compile(r"\b(?:twice|half|double|triple|three times|four times|ten times|a third|a quarter)\b", re.I)
LIST_MARKER = re.compile(r"(?:^|(?<=\s))\d{1,2}\)(?=\s)")
SUM_WORDS = re.compile(
    r"\b(?:how (?:much|many|long|far|old|fast|heavy)|total|altogether|in all|costs?|price|pay|save|saving|discount\w*|"
    r"percent|per ?cent|interest|compound\w*|average|mean|sum|product|times|multipl\w*|divid\w*|plus|minus|squared|"
    r"cubed|square root|area|perimeter|volume|speed|rate|ratio|split|shared?|each|left|remain\w*|profit|loss|vat|tax|"
    r"uses?|fuel|journey|takes?|arrive\w*|leaves?|reduced|added)\b|[%×÷*/+^=£$€]", re.I)
WRITTEN_SUM = re.compile(r"\d\s*(?:[-+*/×÷^x]|\*\*)\s*\(?\d")
ONE_NUMBER_SUM = re.compile(r"\b(?:square root|squared|cubed|factorial|to the power|(?:percent|per ?cent|%) of|"
                            r"how many (?:seconds|minutes|hours|days|weeks|months) (?:are )?(?:there )?in)\b", re.I)
WEEKDAY = r"(?:mon|tues|wednes|thurs|fri|satur|sun)day"
MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*"
DATE_WORK = re.compile(
    r"\b(?:what|which) day (?:of the week )?(?:was|is|will|did|does)\b.*\b\d{1,2}(?:st|nd|rd|th)?\b|"
    r"\bhow many (?:days|weeks|months|years) (?:are there )?(?:between|from|since|before|after)\b.*\d|"
    r"\b\d+ (?:days|weeks|months|years) (?:after|before|from|later|earlier)\b.*\d|"
    r"\b(?:what|which) day (?:of the week )?(?:was|is|will|did|does)\b.*\b[12]\d{3}\b|"
    r"\bleap year\b|\b(?:days|weeks) (?:later|after|before)\b|"
    r"\b(?:what|which) day\b.*\bin \d[\d,]* (?:days|weeks)\b|"
    rf"\b(?:first|second|third|fourth|fifth|last) {WEEKDAY} (?:of|in) {MONTH}\b|"
    rf"\bhow many {WEEKDAY}s\b", re.I)
OPTION = re.compile(r"(?:^|\s|\()([A-Ea-e])[).:]\s*\S")
QUIZ_WORDS = re.compile(r"\b(?:quiz|multiple[- ]choice|pub quiz|trivia|true or false)\b", re.I)
RELATIVES = r"(?:sisters?|brothers?|siblings?|daughters?|sons|cousins?|uncles?|aunts?|nephews?|nieces?)"
RATE_PUZZLE_SUBJECTS = r"(?:machines|workers|people|men|women|hens|chickens|cats|monkeys|painters|builders)"
WORD_PUZZLE = re.compile(
    r"\b(?:riddle|puzzle|logic problem|brain ?teaser|trick question)\b|"
    rf"\bhow many {RELATIVES}\b|"
    r"\b(?:doubles|triples|halves)\b.{0,30}\bevery\b|"
    rf"\b\d+ {RATE_PUZZLE_SUBJECTS}\b.{{0,300}}\b(?:takes?|makes?|lays?|paints?|builds?|eats?|digs?)\b.{{0,300}}"
    rf"\b\d+ {RATE_PUZZLE_SUBJECTS}\b|"
    r"\bheads\b.{0,300}\blegs\b|\blegs\b.{0,300}\bheads\b|"
    r"\bcosts? [£$€]?\d[\d.,]*p? more than\b|"
    r"\b(?:twice|three times|half) as old as\b|\byears? ago\b.{0,300}\bhow old\b|"
    r"\b(?:fills?|empties|drains?)\b.{0,300}\b(?:together|at the same time|plug)\b|"
    r"\btowards each other\b|"
    r"\bknights?\b.{0,300}\bknaves?\b|\b(?:always|only ever) (?:lies|tells the truth)\b|"
    r"\b(?:sits?|sitting|seated|stands?|standing)\b.{0,300}\b(?:left|right) of\b|\bimmediately (?:in front of|behind|left of|right of)\b",
    re.I)
HYPOTHETICAL = re.compile(r"\bif \w+ (?:has|have|had|is|are)\b.*\bhow\b", re.I)
WRITING_JOB = re.compile(r"\b(?:summar(?:y|ies|i[sz]e\w*)|rewrite|reword|rephrase|paraphrase|translate|proofread)\b", re.I)
NOT_A_SUM = re.compile(r"\b(?:python|program|function|script|code|regex|sql|https?://|attached|ip address|port)\b|\(the user attached",
                       re.I)
MIN_OPTIONS = 3
QUESTION_CHARS = 2000


def question_text(text: str) -> str:
    return (text or "")[:QUESTION_CHARS]


def numbers_in(text: str) -> int:
    counted = LIST_MARKER.sub(" ", text or "")
    return len(NUMBER.findall(counted)) + len(NUMBER_WORDS.findall(counted))


def is_a_sum(text: str) -> bool:
    text = text or ""
    if WRITTEN_SUM.search(text) or (numbers_in(text) >= 1 and ONE_NUMBER_SUM.search(text)):
        return True
    return numbers_in(text) >= 2 and bool(SUM_WORDS.search(text))


def is_date_work(text: str) -> bool:
    return bool(DATE_WORK.search(text or ""))


def option_letters(text: str) -> list[str]:
    return [letter.upper() for letter in OPTION.findall(text or "")]


def is_multiple_choice(text: str) -> bool:
    letters = option_letters(text)
    return len(set(letters)) >= MIN_OPTIONS and letters[:2] == ["A", "B"]


def is_quiz(text: str) -> bool:
    return is_multiple_choice(text) or bool(QUIZ_WORDS.search(text or ""))


def is_word_puzzle(text: str) -> bool:
    return bool(WORD_PUZZLE.search(text or ""))


def is_not_for_a_program(text: str) -> bool:
    text = text or ""
    return bool(NOT_A_SUM.search(text) or WRITING_JOB.search(text)) or is_quiz(text) or is_word_puzzle(text)


def needs_exact_work(text: str) -> bool:
    text = question_text(text)
    if is_not_for_a_program(text):
        return False
    return is_a_sum(text) or is_date_work(text)


def is_puzzle(text: str) -> bool:
    if is_word_puzzle(text):
        return True
    return bool(HYPOTHETICAL.search(text or "")) and not needs_exact_work(text)


def needs_reasoning(text: str, kinds=()) -> bool:
    text = question_text(text)
    return any(KINDS[kind](text) for kind in kinds if kind in KINDS)


KINDS = {"quiz": is_quiz, "puzzle": is_puzzle}
CHECKS = {"needs_exact_work": needs_exact_work}

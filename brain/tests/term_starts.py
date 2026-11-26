import re
import re._constants as sre
import re._parser as parser
import string

ANY_CHAR = "*"
WORD_CHARS = frozenset(string.ascii_lowercase + string.digits + "_")
SPACE_CHARS = frozenset(" \t\n\r\f\v")
DIGITS = frozenset(string.digits)
CATEGORIES = {sre.CATEGORY_WORD: WORD_CHARS, sre.CATEGORY_SPACE: SPACE_CHARS, sre.CATEGORY_DIGIT: DIGITS}
REPEATS = (sre.MAX_REPEAT, sre.MIN_REPEAT, sre.POSSESSIVE_REPEAT)
ZERO_WIDTH = (sre.ASSERT, sre.ASSERT_NOT, sre.AT)


def chars_of(op, value) -> frozenset:
    if op is sre.LITERAL:
        return frozenset(chr(value).lower())
    if op is sre.ANY:
        return frozenset(ANY_CHAR)
    chars = set()
    for item_op, item in value:
        if item_op is sre.NEGATE:
            return frozenset(ANY_CHAR)
        if item_op is sre.LITERAL:
            chars.add(chr(item).lower())
        elif item_op is sre.RANGE:
            chars.update(chr(code).lower() for code in range(item[0], item[1] + 1))
        elif item_op is sre.CATEGORY:
            chars.update(CATEGORIES.get(item, {ANY_CHAR}))
    return frozenset(chars)


def starts(items, length: int) -> set[tuple]:
    if length == 0 or not items:
        return {()}
    (op, value), rest = items[0], items[1:]
    if op in ZERO_WIDTH:
        return starts(rest, length)
    if op in (sre.LITERAL, sre.IN, sre.ANY):
        return {(chars_of(op, value), *tail) for tail in starts(rest, length - 1)}
    if op is sre.SUBPATTERN:
        return starts(list(value[3]) + rest, length)
    if op is sre.BRANCH:
        return {found for branch in value[1] for found in starts(list(branch) + rest, length)}
    if op in REPEATS:
        low, high, sub = value
        found = starts(rest, length) if low == 0 else set()
        once = list(sub)
        more = [(op, (max(low - 1, 0), high, sub))] if high is sre.MAXREPEAT or high > 1 else []
        return found | starts(once + more + rest, length)
    if op is sre.GROUPREF or op is sre.GROUPREF_EXISTS:
        return {(frozenset(ANY_CHAR),) * length}
    raise ValueError(f"unhandled pattern part {op}")


def term_starts(pattern: str, length: int = 2) -> set[tuple]:
    return starts(list(parser.parse(pattern, re.IGNORECASE)), length)

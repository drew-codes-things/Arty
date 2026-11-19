import re
import unicodedata

MAX_SPACES_IN_A_ROW = 3
HIDDEN_CATEGORY_GROUPS = ("C", "Z")
INVISIBLE_MARK_CATEGORIES = ("Mn", "Me")
BLANK_LOOKING_CODE_POINTS = frozenset({0x115F, 0x1160, 0x2800, 0x3164, 0xFFA0, 0x1D159})
DEFAULT_IGNORABLE_RANGES = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
    (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164),
    (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
)
_TOO_MANY_SPACES = re.compile(" {%d,}" % (MAX_SPACES_IN_A_ROW + 1))


def is_default_ignorable(character):
    code_point = ord(character)
    return any(first <= code_point <= last for first, last in DEFAULT_IGNORABLE_RANGES)


def is_hidden(character):
    if character == " ":
        return False
    category = unicodedata.category(character)
    return (category[0] in HIDDEN_CATEGORY_GROUPS or category in INVISIBLE_MARK_CATEGORIES
            or ord(character) in BLANK_LOOKING_CODE_POINTS or is_default_ignorable(character))


def has_hidden_text(text):
    return bool(_TOO_MANY_SPACES.search(text)) or any(is_hidden(character) for character in text)


def is_shown_exactly(text):
    return bool(text) and text == text.strip(" ") and not has_hidden_text(text)

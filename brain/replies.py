import logging
import re

import owner

from reply_rules import URL_TAGS, PROMPT_LEAK, FUMBLED_CALL, TOOL_META_SENTENCE, JSON_BLOCK, TOOLS_NOTE_ECHO
from steering import LOOKUP_LABEL
from connections import USER_NAME

log = logging.getLogger("brain")


NAME_GREETING, NAME_TAIL = owner.vocative_patterns(USER_NAME)


def tidy_answer(answer: str) -> str:
    if leak := PROMPT_LEAK.search(answer):
        log.warning("cut a reply that ran on into its instructions")
        answer = answer[:leak.start()].rstrip()
    if answer.count("```") % 2 == 1 and (fumbled := FUMBLED_CALL.search(answer)):
        answer = answer[:fumbled.start()]  # an unclosed block would turn the rest of the chat into code
    answer = TOOLS_NOTE_ECHO.sub("", LOOKUP_LABEL.sub("", answer))
    answer = TOOL_META_SENTENCE.sub("", JSON_BLOCK.sub("\n", answer))
    answer = re.sub(r"\n{3,}", "\n\n", answer)  # no strip: this also cleans early sentences, whose spaces matter
    answer = NAME_GREETING.sub(lambda m: m.group(1), answer)
    answer = NAME_TAIL.sub("", answer)
    return URL_TAGS.sub("", answer)


def as_written(text: str) -> str:
    return text


def never_an_offer(_sentence: str) -> None:
    return None

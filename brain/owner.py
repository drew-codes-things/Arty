import json
import re

FALLBACK_NAME = "the user"
OWNER_TOKEN = "OWNER"
GREETING_WORDS = ("hi|hello|hey|morning|evening|goodnight|good night|good morning|good evening|good afternoon|"
                  "thanks|thank you|night|cheers|sure|okay|ok|alright|of course")


def configured_name(config_file: str) -> str:
    try:
        with open(config_file, encoding="utf-8") as handle:
            name = json.load(handle).get("user_name", "")
    except FileNotFoundError:
        return FALLBACK_NAME
    return str(name or "").strip() or FALLBACK_NAME


def fill(template: str, name: str) -> str:
    return template.replace(OWNER_TOKEN, name)


def vocative_patterns(name: str) -> tuple[re.Pattern, re.Pattern]:
    literal = re.escape(name)
    greeting = re.compile(rf"\b({GREETING_WORDS})(,?) {literal}\b", re.IGNORECASE)
    tail = re.compile(rf",\s*{literal}(?=\s*[!.?])")
    return greeting, tail

import random
import re
from datetime import datetime

from archive import message_text

BARE_GREETING = re.compile(
    r"^[\W_]*(?:hi|hiya|hello|hey|heya|howdy|ello|good (?:morning|afternoon|evening)|morning|afternoon|evening)"
    r"(?:\s+(?:there|again|mate|pal|you))?(?:[\s,]+arty)?[\W_]*$", re.IGNORECASE)
BY_TIME_OF_DAY = {
    "morning": ("Morning! Hope you slept alright. What's on today?",
                "Morning. Kettle on yet? What are we starting with?",
                "Morning! Good to hear from you. What's the plan for today?"),
    "afternoon": ("Afternoon! How's the day treating you?",
                  "Hiya, good afternoon. How's it going?",
                  "Afternoon. Good to hear from you. What are you up to?"),
    "evening": ("Evening! How was your day?",
                "Evening. Winding down, or is there more to get through tonight?",
                "Hiya, good evening. How did today go?"),
    "late": ("Hiya. You're up late. What's keeping you going?",
             "Evening, or is it morning now? Either way, I'm here. What's up?",
             "Hiya. Burning the midnight oil? What's on your mind?"),
}
MORNING_STARTS, AFTERNOON_STARTS, EVENING_STARTS, LATE_STARTS = 5, 12, 18, 23


def is_bare_greeting(text: str) -> bool:
    return bool(BARE_GREETING.match(text or ""))


def answers_a_question(messages: list[dict]) -> bool:
    earlier = [message for message in messages[:-1] if message.get("role") in ("user", "assistant")]
    if not earlier or earlier[-1].get("role") != "assistant":
        return False
    last_line = message_text(earlier[-1]).strip().rsplit("\n", 1)[-1]
    return "?" in last_line


def time_of_day(hour: int) -> str:
    if MORNING_STARTS <= hour < AFTERNOON_STARTS:
        return "morning"
    if AFTERNOON_STARTS <= hour < EVENING_STARTS:
        return "afternoon"
    if EVENING_STARTS <= hour < LATE_STARTS:
        return "evening"
    return "late"


def reply(now: datetime, choose=random.choice) -> str:
    return choose(BY_TIME_OF_DAY[time_of_day(now.hour)])

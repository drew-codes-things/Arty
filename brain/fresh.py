import re
from collections import Counter

REPLIES_TO_READ = 8
RECENT_REPLIES = 5
OPENER_WORDS = 3
PHRASE_WORDS = 4
MIN_MEANINGFUL_WORDS = 2
MAX_NAMED = 4
MAX_TOPICS = 3
MIN_TOPIC_LETTERS = 5
FILLER_SHARE = 0.4
MIN_REPLIES_FOR_HABITS = 3

GRAMMAR_WORDS = set("""a an the and or but so if then than as at by for from in into of off on onto out over to up
with i me my mine we us our you your yours he him his she her it its they them their this that these those is am are
was were be been being do does did have has had will would can could shall should may might must not no yes oh ah
yeah""".split())
EVERYDAY_WORDS = set("""about after again almost already always anyway around because before better between
bit both cheers course definitely doing done each easy either enough every everything fine first glad going gonna
great guess happy here hope just keep kind know later least let little lovely make maybe means mind more most much
need never next nice nothing only other pretty quite really right said same seems should since something sorry sound
sounds still sure thanks thing things think though time today tomorrow tonight totally trying very want well what
when where which while will wish wonder worth would yours""".split())
FILLERS = ["lol", "haha", "honestly", "basically", "literally", "actually", "to be fair", "fair enough", "no worries",
           "nice one", "sounds good", "for sure", "at the end of the day", "just saying"]
WORD = re.compile(r"[a-z0-9']+")


def message_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, list):
        content = " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return str(content or "")


def words_of(text: str) -> list[str]:
    return WORD.findall(text.lower())


def recent_replies(conversation: list[dict]) -> list[str]:
    replies = [message_text(m) for m in conversation if m.get("role") == "assistant"]
    return [reply for reply in replies if reply.strip()][-REPLIES_TO_READ:]


def meaningful_count(phrase: str) -> int:
    return sum(word not in GRAMMAR_WORDS for word in phrase.split())


def openers(replies: list[str]) -> list[str]:
    starts = [" ".join(words_of(reply)[:OPENER_WORDS]) for reply in replies]
    counted = Counter(start for start in starts if len(start.split()) == OPENER_WORDS)
    return [start for start, uses in counted.most_common(MAX_NAMED) if uses >= 2]


def phrases_by_reply(replies: list[str]) -> dict[str, set[int]]:
    found: dict[str, set[int]] = {}
    for index, reply in enumerate(replies):
        words = words_of(reply)
        for start in range(len(words) - PHRASE_WORDS + 1):
            phrase = " ".join(words[start:start + PHRASE_WORDS])
            if meaningful_count(phrase) >= MIN_MEANINGFUL_WORDS:
                found.setdefault(phrase, set()).add(index)
    return found


def overlaps_any(phrase: str, chosen: list[str]) -> bool:
    return any(len(set(phrase.split()) & set(other.split())) >= MIN_MEANINGFUL_WORDS for other in chosen)


def repeated_phrases(replies: list[str]) -> list[str]:
    reply_starts = {" ".join(words_of(reply)[:PHRASE_WORDS]) for reply in replies}
    best_per_habit: dict[frozenset, str] = {}
    for phrase, where in phrases_by_reply(replies).items():
        if len(where) < 2 or phrase in reply_starts:
            continue
        habit = frozenset(where)
        current = best_per_habit.get(habit)
        if current is None or meaningful_count(phrase) > meaningful_count(current):
            best_per_habit[habit] = phrase
    chosen: list[str] = []
    for _, phrase in sorted(best_per_habit.items(), key=lambda item: -len(item[0])):
        if not overlaps_any(phrase, chosen):
            chosen.append(phrase)
    return chosen[:MAX_NAMED]


def looks_like_a_subject(word: str) -> bool:
    return (len(word) >= MIN_TOPIC_LETTERS or word.isdigit()) and word not in EVERYDAY_WORDS and word not in GRAMMAR_WORDS


def topics(replies: list[str], user_message: str = "") -> list[str]:
    if len(replies) < MIN_REPLIES_FOR_HABITS:
        return []
    latest = replies[-RECENT_REPLIES:]
    counted = Counter(word for reply in latest for word in set(words_of(reply)) if looks_like_a_subject(word))
    needed = 3 if len(latest) >= 4 else 2
    raised_by_user = set(words_of(user_message))
    return [word for word, uses in counted.most_common() if uses >= needed and word not in raised_by_user][:MAX_TOPICS]


def filler_pattern(filler: str) -> re.Pattern:
    return re.compile(r"\b" + re.escape(filler) + r"\b")


def fillers(replies: list[str]) -> list[str]:
    if len(replies) < MIN_REPLIES_FOR_HABITS:
        return []
    latest = [reply.lower() for reply in replies[-RECENT_REPLIES:]]
    return [filler for filler in FILLERS
            if sum(bool(filler_pattern(filler).search(reply)) for reply in latest) / len(latest) >= FILLER_SHARE]


def quoted(items: list[str], ending: str = "") -> str:
    return ", ".join(f'"{item}{ending}"' for item in items)


def repetition_note(conversation: list[dict], user_message: str) -> str:
    replies = recent_replies(conversation)
    if len(replies) < 2:
        return ""
    reminders = []
    if started := openers(replies):
        reminders.append(f"several of your last replies opened with {quoted(started, '...')}, so open differently")
    if phrases := repeated_phrases(replies):
        reminders.append(f"these phrases have turned up again and again, so say them another way: {quoted(phrases)}")
    if subjects := topics(replies, user_message):
        reminders.append(f"you keep bringing up {quoted(subjects)}, so drop it unless the user mentions it")
    if tics := fillers(replies):
        reminders.append(f"you're overusing {quoted(tics)}")
    if not reminders:
        return ""
    return ("\n\n(Arty's own note, the user didn't write this. Your replies are getting samey: " + "; ".join(reminders)
            + ". Reply to this message on its own terms.)")

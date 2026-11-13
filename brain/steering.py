import json
import re

import fresh
import models
from settings import PUBLIC_URL
from reply_rules import TOOL_FAILED


# Tools the brain may run by itself before the model answers (see look_up_first): read-only ones only.
LOOKUP_TOOLS = {"homelab__proxmox_status", "homelab__service_status", "weather", "calendar_events", "deadlines_show",
                "list_show", "quiz_progress", "week_recap", "research__web_search", "workspace__gmail_unread", "date_facts", "place_time",
                "currency_facts", "workspace__look_at_desktop", "homelab__pufferpanel_servers", "list_scheduled"}
# In a lookup rule's arguments, this stands for the user's message (e.g. the web search query).
MESSAGE_PLACEHOLDER = "{message}"
# Polite padding that only makes a search query worse.
QUERY_PADDING = re.compile(r"^(hey |hi )?(arty,? )?(can|could|would) you (please )?(tell me|show me|find( me)?|let me know)?\s*|\bplease\b", re.I)
MAX_LOOKUPS = 2
HOMELAB_STATUS_TOOL = "homelab__proxmox_status"
LOOKUP_TEMPERATURE = 0.15
HARD_REQUEST = re.compile(r"\b(think (it |this |that )?(through|hard|carefully)|step[- ]by[- ]step|work out|figure out|calculate|"
                          r"solve|puzzle|riddle|prove|plan (out |my |a |the )?|compare|pros and cons|"
                          r"trade-?offs?|debug|why (is|does|do|did|won'?t|isn'?t|doesn'?t)|optimi[sz]e|design|strategy|"
                          r"best way|what if|estimate)\b", re.I)
MULTI_STEP_REQUEST = re.compile(r"\b(and then|after that|once (that'?s|it'?s) done|first\b.{3,80}\bthen|"
                                r"(build|create|make|write|code) (me )?(an? |the )?(\w+ )?(app|bot|website|site|program|tool|game|"
                                r"script|extension|server|api|dashboard)s?|set up|install|automate|orchestrat\w*|migrate|"
                                r"refactor|research)\b", re.I)
THINKING_MIN_TOKENS = 6144
WRITE_ONLY_REQUEST = re.compile(r"^\W*(?:(?:hey |hi )?arty,?\s*)?(?:please |can you |could you |would you )?(?:write|give me|show me|"
                                r"what'?s|what is|how do i)\b.*\b(?:one-?liner|command|script|function|code|regex|snippet|query|alias)\b", re.I)
RUN_REQUEST = re.compile(r"\b(run|execute|test|try (it|this|that)|on your (computer|desktop|pc)|check (it|that) works)\b", re.I)
RUN_TOOLS = {"workspace__run_command", "workspace__read_terminal", "workspace__list_files", "workspace__read_file",
             "workspace__write_file", "workspace__edit_file"}
WRITE_ONLY_NOTE = "\n\n(Reply with the code itself. Don't run anything or look around your computer for this.)"
WRITE_AND_RUN_NOTE = "\n\n(Write it, then run it on your computer with run_command and show what it printed.)"
HOW_TO_REQUEST = re.compile(r"^\W*(?:(?:hey |hi )?arty,?\s*)?(?:(?:please |can you |could you |would you )?(?:tell me |show me |explain )?"
                            r"how (?:do|can|could|would|should) (?:i|we|one)|(?:(?:please |can you |could you |would you )?"
                            r"(?:tell me |show me |explain )?)how to|what'?s the (?:best )?(?:command|way) to)\b", re.I)
RUN_IT_FOR_ME = re.compile(r"\b(?:on your (?:computer|desktop|pc|vm)|try (?:it|this|that)|(?:run|execute|test) it|"
                           r"check (?:it|that) works)\b", re.I)
HOW_TO_NOTE = ("\n\n(They asked how to do it: explain how, with any commands in a code block. Don't run anything on your "
               "computer for this.)")
GIVEN_LINK = re.compile(r"https?://\S+", re.I)
READ_LINK_REQUEST = re.compile(r"\b(read|summari[sz]e|summary|tl;?dr|what (?:is|does|'s)|tell me|explain|about|says?|key points)\b",
                               re.I)
READ_LINK_NOTE = "\n\n(The user gave a link: read it with {tool} before answering.)"


def asks_how_to(text: str) -> bool:
    return bool(HOW_TO_REQUEST.search(text)) and not RUN_IT_FOR_ME.search(text)


def asks_to_read_a_link(text: str) -> bool:
    return bool(GIVEN_LINK.search(text)) and bool(READ_LINK_REQUEST.search(GIVEN_LINK.sub(" ", text)))


def thinking_for(config, request_text="", writing_job=False) -> bool:
    setting = config.get("think", False)
    if setting != "auto":
        return bool(setting)
    model = models.find(config["model"])
    if not model or not model.get("thinking"):
        return False
    request_text = request_text or ""
    return writing_job or bool(HARD_REQUEST.search(request_text) or MULTI_STEP_REQUEST.search(request_text))


def steer_last_request(history, note: str):
    for index in range(len(history) - 1, -1, -1):
        if history[index].get("role") == "user":
            if isinstance(history[index].get("content"), str):
                history[index] = {**history[index], "content": history[index]["content"] + note}
            return


def steer_away_from_repeats(history: list[dict], conversation: list[dict], newest_request: str, background: bool):
    if not background:
        steer_last_request(history, fresh.repetition_note(conversation, newest_request))


def with_thinking(body, think: bool, raises_reply_size=True):
    options = body.get("options") or {}
    if think and raises_reply_size:
        options = {**options, "num_predict": max(int(options.get("num_predict", 0)), THINKING_MIN_TOKENS)}
    return {**body, "think": think, "options": options}


# Longer messages are mostly pasted text (an error, a document), where a keyword means nothing.
LOOKUP_MAX_MESSAGE_CHARS = 300
# The labels the looked-up results carry in the notes, which small models sometimes copy into the reply.
LOOKUP_LABEL = re.compile(r"[ \t]*:?[ \t]*\[(?:" + "|".join(sorted(LOOKUP_TOOLS)) + r")\]")
LOOKUP_CHARS = 2500


# A lookup that didn't work: an error, or the desktop's browser not answering.
LOOKUP_FAILED = re.compile(TOOL_FAILED.pattern + r"|isn't running|is paused|not reachable|timed out|isn't set up", re.I)


def on_my_desktop(config) -> str:
    console = str(config.get("desktop_console") or "").strip()
    return f"On my desktop ({console})" if console else "On my desktop"


def sign_in_help(config) -> str:
    return (f"I can't open your Gmail right now: the Gmail window on my desktop isn't signed in to Google. "
            f"{on_my_desktop(config)}, double-click the \"Sign in to Google (Gmail)\" icon and sign in once, leave that "
            "window open, and ask me again.")


def with_homelab_names(lookups: list[dict], names) -> list[dict]:
    if not names:
        return lookups
    rule = {"pattern": r"\b(" + "|".join(map(re.escape, names)) + r")\b", "tool": HOMELAB_STATUS_TOOL}
    tools = [entry.get("tool") for entry in lookups]
    place = tools.index(HOMELAB_STATUS_TOOL) + 1 if HOMELAB_STATUS_TOOL in tools else len(lookups)
    return lookups[:place] + [rule] + lookups[place:]


def matches_lookup(config, said: str) -> bool:
    return len(said) <= LOOKUP_MAX_MESSAGE_CHARS and any(
        rule.get("tool") in LOOKUP_TOOLS and re.search(rule.get("pattern", "(?!)"), said, re.I) for rule in config.get("lookups", []))


def with_context(history: list[dict], context: str):
    """Puts the per-message context at the start of the latest user message. Not as a system message:
    Ollama merges every system message into the one block above the tool list, so a changing one there
    makes the model re-read the whole prompt (some 4,000 tokens, about 9 seconds) on every message.
    The message is replaced, not edited: it's shared with the conversation that gets saved."""
    note = f"<notes>\nYour notes for this message (not written by the user):\n{context}\n</notes>\n\n"
    last_user = max((i for i, m in enumerate(history) if m.get("role") == "user"), default=None)
    if last_user is None:
        history.append({"role": "user", "content": note.strip()})
    else:
        history[last_user] = {**history[last_user], "content": note + str(history[last_user].get("content") or "")}


def parse_arguments(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def task_links(task: dict, actions: list[tuple[str, str]]) -> list[dict]:
    """Buttons for a paused task: a callback ("t:<id>:<action>") the phone and desktop apps answer through the task
    API, and the equivalent home-network /hooks link."""
    return [{"label": label, "callback": f"t:{task['id']}:{action}",
             "url": f"{PUBLIC_URL}/hooks/tasks/{task['id']}/{action}?token={task['token']}"} for label, action in actions]


ON_THE_WEB = re.compile(r"\b(site|website|web|browser|desktop|page|account|google|gmail|linkedin|captcha|\w+\.(com|co|org|net|io|uk))\b",
                        re.IGNORECASE)
LOGIN_WORDS = re.compile(r"sign in|log ?in|captcha|bot check|verif|human", re.IGNORECASE)


# Tools that work on the desktop (browser, Gmail window, screen, terminal): only after one of them can a website have
# asked for a sign-in or a CAPTCHA.
DESKTOP_TOOLS = ("workspace__",)
NOTHING_TO_SIGN_IN = ("Nothing on the desktop was used, so there's nothing for the user to sign in to or pass. "
                      "Don't use ask_user; reply to the user directly.")


def question_for_user(message: str, config) -> str:
    """What Arty says in chat when it needs the user to do something on its desktop."""
    text = message.strip().rstrip(".") + "."
    if LOGIN_WORDS.search(text) and ON_THE_WEB.search(text):  # not "sign in to your phone": that's a confused model
        text += (f" {on_my_desktop(config)}, double-click \"Sign in to websites\", "
                 "sign in, then close that window. Tell me when it's done and I'll carry on.")
    return text

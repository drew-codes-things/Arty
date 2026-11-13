import re


# Trailing offers the user asked not to get ("Would you like...?", "Let me know if..."); small models add them anyway.
OFFER_SENTENCE = re.compile(
    r"(would you like|do you want me to|do you want to|shall i|should i|ready to proceed|is there anything else|anything else i can|"
    r"let me know if|feel free to|if you need anything|if you decide|just let me know|how can i (help|assist)|"
    r"what would you like|anything (specific|else) you need|need any (other|more|further) (help|assistance)|"
    r"want to explore|explore this further|happy to help|here to help|have a (good|great|nice) (day|night|evening|week)|"
    r"what would you prefer|which would you prefer|or do you have (a|any)|if you('d| would)? (want|like)( me)? to|"
    r"i can (also )?(find|fetch|look|search|get|check)( some| more)?|i hope (this|that) helps|hope this helps|"
    r"if you need (more|any more|any further|further|other|any)|we can (look into|explore|check)|let me know how|"
    r"let's move on|if so, (please )?(provide|tell me|give me|let me know)|what would be a good time|i can check again|"
    r"what else can i (help|assist|do)|anything else (for|i can)|would you like to (add|explore|know|learn|see)|"
    r"i can help (you )?(with that|set|schedule|add)|you can check your \w+ directly|otherwise, everything is|"
    r"(questions|tasks) i can help with|can help with today|what do you prefer|if (that|this) would (be useful|help)|"
    r"(an honour|a pleasure) to (assist|help)|happy to assist|i'd be (happy|glad) to)", re.IGNORECASE)


def trim_offers(answer: str) -> str:
    """Drop the sentences at the very end that only offer more help. Only the last paragraph is touched,
    so lists and structure stay as they are, and something of the answer always remains."""
    paragraphs = answer.strip().split("\n\n")
    while paragraphs:
        sentences = re.split(r"(?<=[.!?])\s+", paragraphs[-1].strip())
        while sentences and OFFER_SENTENCE.search(sentences[-1]):
            sentences.pop()
        if sentences:
            paragraphs[-1] = " ".join(sentences)
            break
        if len(paragraphs) == 1:
            return answer.strip()  # the whole answer was an offer: leave it
        paragraphs.pop()
    return "\n\n".join(paragraphs)


URL_TAGS = re.compile(r"</?url>", re.IGNORECASE)
TOOLS_NOTE_ECHO = re.compile(r"[ \t]*(?:\[\s*|<context>\s*)?(?:Tools you used or tried on their computer|"
                             r"For your reply above, you used or tried these tools on their computer)[^\]\n<]*(?:\]|</context>)?",
                             re.IGNORECASE)
# Small models sometimes run on past their answer into their own instructions (the time line, the tool list).
PROMPT_LEAK = re.compile(r"((?:^|\n)\s*It is now \w+day \d+ \w+ \d{4}, \d\d:\d\d \([^()\n]*, UTC[+-]\d\d:\d\d\)|\n\s*# Tools\b|<tools>|"
                         r"You may call one or more functions|\n\s*FACTS YOU ALREADY KNOW|\n\s*BACKGROUND ONLY, do not continue|</?notes>|"
                         r"\[\d{4}-\d\d-\d\d \d\d:\d\d\] User:|\n\s*Arty \(you\):)")


# A reply ending by opening a code block with nothing in it: the model began a tool call as text.
FUMBLED_CALL = re.compile(r"\s*```[a-z]*\s*$")


# The user asked for something done, or the reply claims something was done or found: with no tool
# used, the small model is describing results it never got (a reminder that was never set, "findings
# from my web searches" it never ran). It's sent back once to actually do it.
ACTION_REQUEST = re.compile(r"\b(remind me|look (it |that |this )?up|search|research|find out|google|look into|check (on |the |my )?|"
                            r"use your (desktop|computer|pc|browser)|schedule|set (a|an|up)|send (me|it)|what'?s the (weather|news|latest|price))\b", re.I)
CLAIMED = re.compile(r"\b(I('ve| have) (just )?(checked|searched|looked|found|set|scheduled|created|sent|opened|added|researched)|"
                     r"here are (some|the) (findings|results)|from my (recent )?(web )?search(es)?|I found (that|some|the))", re.I)
# Asking the user what they mean, about something it could have looked up.
CLARIFYING = re.compile(r"(could you (please )?(provide|clarify|specify|give)|more (context|details)|what (exactly )?do you mean|"
                        r"not sure what you('re| are) referring|doesn't appear to be|can you (clarify|explain what))", re.I)
SEARCH_REQUEST = re.compile(r"\b(look (it |that |this )?up|search|research|find out|google|look into|what('s| is| does)|"
                            r"who('s| is)|verdict|mean(s|ing)?|reference)\b", re.I)
# "remind me to ...", "remind to ...", "set a reminder to ..." (but not "it reminds me of ...")
REMIND_REQUEST = re.compile(r"\bremind(?:\s+(?:me|us))?(?=\s+(?:to|about|in|at|on|by|tomorrow|tonight|this|next|every|each)\b)|"
                            r"\b(?:set|make|add|create|put in)\s+(?:me\s+)?(?:a|an)\s+reminder\b", re.I)
# "... on Telegram" / "send it to Discord": left out of the reminder's text; reminders only go to Arty's apps.
CHAT_APP = re.compile(r"[\s,]*(?:\b(?:and\s+)?(?:send|post|put)\s+(?:it|that|them)\s+)?\b(?:on|to|via|through|in|over|and)\s+(?:my\s+)?"
                      r"(telegram|discord)\b(?:\s+(?:too|as well))?", re.I)
REMINDER_PARTS = [
    re.compile(r"\bremind(?: (?:me|us))? (?:to |about )(?P<what>.+?) (?P<when>(?:in|at|on|by|tomorrow|tonight|this|next|every|each)\b.+)$", re.I),
    re.compile(r"\bremind(?: (?:me|us))? (?P<when>(?:in|at|on|tomorrow|tonight|this|next|every|each)\b.*?) (?:to|about) (?P<what>.+)$", re.I),
    re.compile(r"\breminder (?:to |for |about )(?P<what>.+?) (?P<when>(?:in|at|on|by|tomorrow|tonight|this|next|every|each)\b.+)$", re.I),
    re.compile(r"\breminder (?P<when>(?:in|at|on|tomorrow|tonight|this|next|every|each)\b.*?) (?:to|for|about) (?P<what>.+)$", re.I),
]


def reminder_from(text: str) -> tuple[str, str] | None:
    """(what, when) from "remind me to X in 5 minutes" / "remind me in 5 minutes to X", or None."""
    text = CHAT_APP.sub("", text)
    text = re.sub(r"[\s,]*\b(please|pls|plz|thanks|thank you|cheers|ta|mate)\W*$", "", text.strip(), flags=re.I).rstrip(".!?")
    for pattern in REMINDER_PARTS:
        if match := pattern.search(text):
            what = match["what"].strip()
            return what[:1].upper() + what[1:] + ".", match["when"].strip()
    return None


PROMISE = re.compile(r"\b(I'll|I will|I've|I have|I'm going to)\s+(remind|set (up )?(a |the |your )?(reminder|alarm|schedule)|schedule|start(ed)? (a |the )?(background )?task)", re.IGNORECASE)
# Talking about its tools ("let's use the `homelab__service_status` function", "I'll run this command"):
# the user never needs to see that, so it isn't sent early (see run_agent).
TOOL_TALK = re.compile(r"\b\w+__\w+|\bfunction\b|\btool\b|I'll run|let me (run|use|call)|let's (use|run|call)", re.I)
# Asking Arty to change something ("put the dentist in my calendar", "add milk to my list"): a read-only lookup
# may run first, but the tools to actually do it must stay on offer.
DO_REQUEST = re.compile(r"^\W*(?:(?:hey |hi )?arty,?\s*)?(?:(?:please|can you|could you|would you|will you|i want you to|"
                        r"i need you to)\s+)*(add|put|book|schedule|set|create|make|remind|cancel|delete|remove|move|change|update|"
                        r"send|save|note|mark|tick|clear|rename|turn|start|stop|restart|open|close|play|pause)\b", re.I)

# A reply that plays at using tools (made-up JSON results, "using the `homelab__x` function") instead of answering.
PRETEND_TOOLS = re.compile(r"```json|\b[a-z]+__[a-z_]+\b|<tool_call>", re.I)
# Sentences that only talk about Arty's own machinery.
TOOL_META_SENTENCE = re.compile(r"[^.!?\n]*(\b(none of the|the provided|the available|my available)\s+(functions|tools)\b|"
                                r"\b[a-z]+__[a-z_]+\b|answer (your question )?directly instead|(with|using) the (tool|function)\b)"
                                r"[^.!?\n]*[.!?:]?", re.I)
# A tool result that says it didn't work: the start of hub.call's errors, MCP errors and native refusals.
TOOL_FAILED = re.compile(r"^\W*(unknown tool|tool error|tool unavailable|error\b|failed\b|blocked\b|(i )?(couldn't|could not|can't|cannot)\b)", re.I)
FAILED_NOTE = ("\n\n(That didn't work. Tell the user plainly that it failed and why, in a sentence; never say it worked "
               "and never make up a result.)")
# Tools that do things; read-only lookups and unlocking tool groups don't count as having done anything.
NOT_ACTIONS = {"use_tools"}
LOOK_ONLY_TOOLS = {"chrome_tabs", "chrome_read", "chrome_find", "chrome_scroll", "chrome_look", "computer_look", "computer_apps",
                   "computer_read_file", "computer_list_files", "computer_find_files", "computer_search", "read_clipboard",
                   "desktop_windows", "desktop_notifications", "system_status", "code_sandbox"}
NOT_FOR_ME = "(not for me)"
VOICE_MODE = "(voice mode)"
ADDRESSED_PROMPT = ("A home voice assistant called Arty heard this through a room microphone, either right after its "
                    "wake word or in the few seconds after it last spoke. Decide whether the person was talking TO Arty "
                    "(a question, request, command, or a reply to what Arty just said) or whether it was overheard: "
                    "people talking to each other, an exclamation, laughter, or a fragment that asks nothing of Arty.\n\n"
                    "What Arty last said: %s\nWhat the microphone heard: %s\n\n"
                    'Reply with JSON only: {"for_arty": true} or {"for_arty": false}')
JSON_BLOCK = re.compile(r"\n?```json[\s\S]*?```\n?", re.I)
# A reply this long that only then asks to unlock tools has already answered (see run_agent).
ANSWERED_ALREADY_CHARS = 200
TOOLLESS_NOTE = ("You are running on a model without tool support: you can't use tools, search, your computer or the browser "
                 "right now. Answer from what you know and from the memories above; if something needs tools, say so briefly.")


# Notes an app attaches to a message for this turn only (the desktop app's <context> and <screen>):
# useful to answer it, but not what the user said, so they're kept out of the archive and memory.
APP_NOTES = re.compile(r"\s*<(context|screen)>.*?</\1>", re.DOTALL)
NOTE_CLIENTS = frozenset({"desktop", "phone"})
NOTE_TAGS = ("context", "screen")


def last_note_start(text: str) -> int:
    for tag in NOTE_TAGS:
        if text.endswith(f"</{tag}>"):
            return text.rfind(f"<{tag}>")
    return -1


def without_app_notes(text: str) -> str:
    text = (text or "").rstrip()
    while (start := last_note_start(text)) != -1:
        text = text[:start].rstrip()
    return text


def user_words(text: str, client: str) -> str:
    return (without_app_notes(text) if client in NOTE_CLIENTS else text or "").strip()

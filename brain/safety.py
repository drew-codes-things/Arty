import re

HOW = r"\b(?:how (?:do|can|could|would|should) (?:i|you|we|one|someone)|how to|ways? to|best way to|help me|teach me|tell me how to)\b"
WEAPON = (r"(?:(?:nuclear|atomic|dirty|pipe|nail|car|fertili[sz]er|home-?made|petrol|molotov)\s+bombs?|"
          r"molotovs?(?:\s+cocktails?)?(?![-'\w])|(?<!bath )(?<!photo )"
          r"(?<!seed )(?<!glitter )\bbombs?|explosives|explosive devices?|ieds?|grenades?|nerve (?:agent|gas)|sarin|vx|ricin|anthrax|"
          r"mustard gas|chlorine gas|napalm|fire-?bombs?|incendiary (?:bombs?|devices?|weapons?)|bio-?weapons?|biological weapons?|chemical weapons?|weapons? of mass destruction|"
          r"(?:3d[- ]printed |untraceable |ghost )guns?)\b")
NOT_THE_WEAPON = (r"calorimeters?|disposal|squads?|shelters?|threats?|detection|detectors?|safety|awareness|drops?|"
                  r"burgers?|sauces?|playlists?|desserts?|cyclones?|scares?|technicians?|proof")
WEAPON_ITSELF = WEAPON + r"(?![-'\w]|\s+(?:" + NOT_THE_WEAPON + r")\b)"
PHRASE_ENDS = (r"(?=\s*(?:[,.;:?!]|$)|\s+(?:please|at home|from scratch|myself|for me|now|step by step|"
               r"in (?:my|our) \w+|in (?:the|a) (?:shed|garage|basement|cellar|attic|kitchen|garden|back ?yard|yard|house|flat|bedroom|woods))\b)")
MAKE_ITSELF = r"\b(?:make|making|build|building|create|construct|assemble|synthesi[sz]e|produce|cook|brew)\b"
OBTAIN = r"\b(?:get|buy)\b"
SHOPPING_TAIL = (r"(?:\s+(?:online|anywhere|near me|cheap|without an? licen[cs]e|from (?:the |a )?[\w.-]+|"
                 r"on the dark ?web)\b)?")
MAKE = r"(?:" + MAKE_ITSELF + r"|" + OBTAIN + r")"
# "how to make a nuclear bomb", "build a pipe bomb", "synthesize sarin", "enrich uranium for a bomb"
WEAPONS = re.compile(
    r"(?:" + HOW + r"|^\s*(?:please\s+)?)[^.?!]{0,20}(?:" + MAKE_ITSELF + r"\s+(?:a |an |some |my own )?" + WEAPON_ITSELF +
    r"|" + OBTAIN + r"\s+(?:a |an |some |my own )?" + WEAPON + SHOPPING_TAIL + PHRASE_ENDS + r")"
    r"|\benrich(?:ing)? uranium\b[^.?!]{0,30}\b(?:bomb|weapon)",
    re.IGNORECASE)
ASKS_FOR_STEPS = (r"\b(?:step[- ]by[- ]step|instructions?|steps|guides?|recipes?|tutorials?|walk-?\s?throughs?|directions|"
                  r"blueprints?|schematics?)\b")
WEAPON_STEPS = re.compile(
    ASKS_FOR_STEPS + r"[^.?!]{0,40}?(?:" + MAKE_ITSELF + r"\s+(?:a |an |some |my own )?" + WEAPON_ITSELF +
    r"|\b(?:for|on|to)\s+(?:a |an )?" + WEAPON + PHRASE_ENDS + r")",
    re.IGNORECASE)
SELF_HARM = re.compile(
    r"\b(?:kill|hurt|harm) myself\b|\bcut myself\b(?!\s+(?:(?:while |when )?(?:shaving|cooking|chopping)|by accident|"
    r"accidentally))|"
    r"\bend (?:it all|my (?:own )?life)\b|\bsuicid(?:e|al)\b(?!\s+(?:squad|rates?|statistics|stats|figures|"
    r"prevention|awareness|bombers?|bombings?|forest)\b)|"
    r"\b(?:don'?t|do not) want to (?:live|be alive|be here) any ?more\b|\bself[- ]harm",
    re.IGNORECASE)
BOT_CHECK = re.compile(
    r"\b(?:pass|solve|complete|beat|bypass|get (?:past|around|round|through)|skip|crack)\b[^.?!]{0,30}\b(?:captchas?|recaptchas?|"
    r"hcaptchas?|bot[- ]checks?|human (?:check|verification)|cloudflare (?:check|challenge))\b",
    re.IGNORECASE)

BOT_CHECK_REPLY = ("I won't get past CAPTCHAs or bot checks, even for you: they're there to make sure a person is at the "
                   "keyboard. If you open that page on my desktop and pass the check yourself, I'll carry on from there.")
WEAPONS_REPLY = ("I can't help with making weapons or explosives. I'm happy to talk about the history or the general "
                 "science instead, like how nuclear power works.")
DEFAULT_CRISIS_HELP = ("Samaritans are free, any time, day or night, on 116 123, or you can text SHOUT to 85258. "
                       "If you're in danger right now, call 999.")


def self_harm_reply(crisis_help: str | None = None) -> str:
    return ("I'm really sorry you're feeling like this, and I'm glad you said something. You don't have to deal "
            f"with it on your own: {crisis_help or DEFAULT_CRISIS_HELP} I'm here if you want to talk.")


ALL_CHECKS = ("self_harm", "weapons", "bot_check")


def check(text: str, crisis_help: str | None = None, kinds: tuple[str, ...] = ALL_CHECKS) -> tuple[str, str] | None:
    """(kind, reply) for a request Arty declines (or a cry for help), or None."""
    text = text or ""
    if "self_harm" in kinds and SELF_HARM.search(text):
        return "self_harm", self_harm_reply(crisis_help)
    if "weapons" in kinds and (WEAPONS.search(text) or WEAPON_STEPS.search(text)):
        return "weapons", WEAPONS_REPLY
    if "bot_check" in kinds and BOT_CHECK.search(text):
        return "bot_check", BOT_CHECK_REPLY
    return None


def check_each(texts, crisis_help: str | None = None) -> tuple[str, str] | None:
    return next((declined for text in texts if (declined := check(text, crisis_help))), None)

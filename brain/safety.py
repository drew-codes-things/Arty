"""Requests Arty declines before the model or the web sees them. A 3B model doesn't refuse reliably (it searched
the web and wrote out "Steps to Make a Nuclear Bomb"), so clear requests to make weapons that can kill many get a
short, plain answer in code. Someone talking about hurting themselves gets care and where to find help, not a
refusal. Narrow on purpose: "bath bomb" and history or news questions pass through. Other harmful requests are left
to the model (system-prompt.md); Drew asked for the code check on hurting people to be removed (2026-10-24).
"""
import re

HOW = r"\b(?:how (?:do|can|could|would|should) (?:i|you|we|one|someone)|how to|ways? to|best way to|help me|teach me|tell me how to)\b"
WEAPON = (r"(?:(?:nuclear|atomic|dirty|pipe|nail|car|fertili[sz]er|home-?made|petrol|molotov)\s+bombs?|(?<!bath )(?<!photo )"
          r"(?<!seed )(?<!glitter )\bbombs?|explosives|explosive devices?|ieds?|grenades?|nerve (?:agent|gas)|sarin|vx|ricin|anthrax|"
          r"mustard gas|chlorine gas|bio-?weapons?|biological weapons?|chemical weapons?|weapons? of mass destruction|"
          r"(?:3d[- ]printed |untraceable |ghost )guns?)\b")
MAKE = r"\b(?:make|making|build|building|create|construct|assemble|synthesi[sz]e|produce|cook|brew|get|buy)\b"
# "how to make a nuclear bomb", "build a pipe bomb", "synthesize sarin", "enrich uranium for a bomb"
WEAPONS = re.compile(
    r"(?:" + HOW + r"|^\s*(?:please\s+)?)[^.?!]{0,20}" + MAKE + r"\s+(?:a |an |some |my own )?" + WEAPON +
    r"|\benrich(?:ing)? uranium\b[^.?!]{0,30}\b(?:bomb|weapon)",
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


def check(text: str, crisis_help: str | None = None) -> tuple[str, str] | None:
    """(kind, reply) for a request Arty declines (or a cry for help), or None."""
    text = text or ""
    if SELF_HARM.search(text):
        return "self_harm", self_harm_reply(crisis_help)
    if WEAPONS.search(text):
        return "weapons", WEAPONS_REPLY
    if BOT_CHECK.search(text):
        return "bot_check", BOT_CHECK_REPLY
    return None

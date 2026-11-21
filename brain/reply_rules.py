import re

import safety


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

SENTENCE_GAP = re.compile(r"(?<=[.!?])\s+")


def trim_offers(answer: str) -> str:
    """Drop the sentences at the very end that only offer more help. Only the last paragraph is touched,
    so lists and structure stay as they are, and something of the answer always remains."""
    paragraphs = answer.strip().split("\n\n")
    while paragraphs:
        if kept := without_trailing_offers(paragraphs[-1].strip()):
            paragraphs[-1] = kept
            break
        if len(paragraphs) == 1:
            return answer.strip()  # the whole answer was an offer: leave it
        paragraphs.pop()
    return "\n\n".join(paragraphs)


def without_trailing_offers(paragraph: str) -> str:
    starts = [0, *(gap.end() for gap in SENTENCE_GAP.finditer(paragraph))]
    while starts and OFFER_SENTENCE.search(paragraph[starts[-1]:]):
        paragraph = paragraph[:starts.pop()].rstrip()
    return paragraph


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


SCHEDULE_REQUEST = re.compile(
    r"\b(?:remind\w*|schedul\w*|alarms?|timers?|every\s+\w+|daily|weekly|monthly|hourly|tomorrow|tonight|later|"
    r"(?:at|by|until|till|from)\s+\d|\d\s*(?:am|pm)|\d[:.]\d\d|o'?clock|noon|midnight|"
    r"in\s+(?:a|an|\d+|half|one|two|three|four|five|ten|fifteen|twenty|thirty|a few|a couple of)\s+(?:\w+\s+)?"
    r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?)|"
    r"(?:on|next|this)\s+(?:mon|tues|wednes|thurs|fri|satur|sun)day|next\s+(?:week|month|year)|"
    r"this\s+(?:morning|afternoon|evening|weekend)|don'?t let me forget|ping me|nudge me|"
    r"let me know (?:at|when|in|tomorrow|tonight)|check (?:back|in) (?:later|in|at|tomorrow)|recurring|repeat(?:ing|edly)?|"
    r"each\s+(?:day|morning|evening|night|week|month))\b", re.IGNORECASE)
OFFERED_A_SCHEDULE = re.compile(r"\b(?:remind\w*|schedul\w*|alarm|timer|every\s+\w+|daily|weekly)\b", re.IGNORECASE)
SCHEDULE_NOT_ASKED = ("Blocked: nothing was scheduled, because the user's message doesn't ask for a reminder or a schedule. "
                      "Don't guess what they wanted: ask them what they meant.")
PROMISE = re.compile(r"\b(I'll|I will|I've|I have|I'm going to)\s+(remind|set (up )?(a |the |your )?(reminder|alarm|schedule)|schedule|start(ed)? (a |the )?(background )?task)", re.IGNORECASE)
ACTION_CLAIM = re.compile(
    r"\bI(?:'ll|'ve|'m going to| will| have| am going to| just)\s+(?:just\s+|now\s+|already\s+|go(?:ne)? ahead and\s+)?"
    r"(?:pass(?:ed)?|sen[dt]|tell|told|let|messag(?:e|ed)|text(?:ed)?|e-?mail(?:ed)?|forward(?:ed)?|post(?:ed)?|call(?:ed)?|"
    r"set|book(?:ed)?|order(?:ed)?|add(?:ed)?|sav(?:e|ed)|not(?:e|ed)|remind(?:ed)?|schedul(?:e|ed)|creat(?:e|ed)|ma(?:k|d)e|"
    r"turn(?:ed)?|start(?:ed)?|cancel(?:l?ed)?|delet(?:e|ed)|remov(?:e|ed)|chang(?:e|ed)|updat(?:e|ed)|open(?:ed)?|"
    r"shar(?:e|ed)|deliver(?:ed)?|contact(?:ed)?|reach(?:ed)? out|ping(?:ed)?)\b", re.IGNORECASE)
# Talking about its tools ("let's use the `homelab__service_status` function", "I'll run this command"):
# the user never needs to see that, so it isn't sent early (see run_agent).
TOOL_TALK = re.compile(r"\b\w+__\w+|\bfunction\b|\btool\b|I'll run|let me (run|use|call)|let's (use|run|call)", re.I)
# Asking Arty to change something ("put the dentist in my calendar", "add milk to my list"): a read-only lookup
# may run first, but the tools to actually do it must stay on offer.
DO_REQUEST = re.compile(r"^\W*(?:(?:hey |hi )?arty,?\s*)?(?:(?:please|can you|could you|would you|will you|i want you to|"
                        r"i need you to)\s+)*(add|put|book|schedule|set|create|make|remind|cancel|delete|remove|move|change|update|"
                        r"send|save|note|mark|tick|clear|rename|turn|start|stop|restart|open|close|play|pause)\b", re.I)

COURSEWORK_WORK = (r"course ?work|home ?work|assignments?|essays?|dissertations?|thesis|theses|lab reports?|"
                   r"literature reviews?|exams?|tests?|quiz(?:zes)?")
COURSEWORK_REQUEST = re.compile(
    r"\b(?:" + COURSEWORK_WORK + r"|assess(?:ed|ments?)|graded|marked|plagiari[sz]\w*|hand (?:it )?in|submit\w*)\b", re.I)
COURSEWORK_DECLINE = re.compile(
    r"\bI(?:['\u2019]m| am)?\s+(?:really\s+)?(?:can['\u2019]?t|cannot|can not|won['\u2019]t|will not|unable to|not able to|"
    r"not going to)\s+(?:help|write|rewrite|reword|paraphrase|rephrase|do|complete|answer|provide|produce|create|assist|"
    r"finish|solve|give)\b", re.I)
ACADEMIC_REASON = re.compile(
    r"\bacademic (?:integrity|dishonesty|honesty|misconduct|polic(?:y|ies)|work|assignments?)\b|\bcheat\w*|"
    r"\bplagiari[sz]\w*|\byour own (?:work|words|understanding)\b|\bas your own\b|\byour work yourself\b", re.I)
HARM_TOPICS = (r"drugs?|narcotics?|controlled substances?|methamphetamine|meth|cocaine|heroin|fentanyl|malware|"
               r"ransomware|viruse?s?|keylog\w*|spy\w*|stalk\w*|tracking|surveil\w*|consent|child\w*|minors?|underage|"
               r"kids?|teen\w*|\d+[- ]years?[- ]olds?|groom\w*|sexual\w*|nudes?|naked|abus\w*|forg(?:e|ed|ery|ing)|"
               r"counterfeit\w*|fraud\w*|scam\w*|phish\w*|threat\w*|intimidat\w*|blackmail\w*|harass\w*|extort\w*|theft|"
               r"steal\w*|stolen|poison\w*|toxic|explosi\w*|bombs?|kill\w*|murder\w*|suicid\w*|self-harm|terror\w*|"
               r"hate|hateful|racis\w*|personal data")
HARM_REASON = re.compile(r"\b(?:illegal|unlawful|against the law|crime|criminal|harm\w*|hurt|danger\w*|unsafe|violen\w*|"
                         r"weapons?|attack\w*|misuse|" + HARM_TOPICS + r")\b", re.I)
REFUSAL_OPENING_CHARS = 300
REWORDING = r"rewrit\w*|re-?word\w*|paraphras\w*|rephras\w*|spin|in (?:my|other|different|new) words"
SIMILARITY_CHECKERS = (r"plagiari[sz]\w*|similarity|turnitin|originality|copyleaks|gptzero|zerogpt|"
                       r"ai(?:[- ]writing)?[- ]detect\w*")
CHECKER_REWRITE_REQUEST = re.compile(
    rf"\b(?:{REWORDING})\b.*\b(?:{SIMILARITY_CHECKERS})|\b(?:{SIMILARITY_CHECKERS}).*\b(?:{REWORDING})\b", re.I | re.S)
REWORDING_REQUEST = re.compile(rf"\b(?:{REWORDING})\b", re.I)
AUTHOR_HIDING = re.compile(r"\bcan(?:['’]t|not) (?:prove|tell|trace|link|match|show)\b|"
                           r"\b(?:trac|link|match|tie)\w* (?:it|this|them|that) (?:back )?to (?:me|my)\b|"
                           r"\b(?:doesn['’]?t|don['’]?t|won['’]?t) match my (?:writing|style)\b", re.I)
THREAT_TEXT = re.compile(r"\b(?:threat\w*|ransom (?:notes?|letters?|messages?|demands?))\b", re.I)
SIMILARITY_CHECKERS_ALONE = (r"\b(?:similarity|turnitin|originality|copyleaks|gptzero|zerogpt|"
                             r"ai(?:[- ]writing)?[- ]detect\w*)\b")
COPYRIGHT_REASON = re.compile(r"\bcopyright\w*|\bintellectual property\b", re.I)
INTEGRITY_REASON = re.compile(
    ACADEMIC_REASON.pattern + "|" + COPYRIGHT_REASON.pattern + r"|\boriginal (?:authors?|work|writers?)\b|"
    r"\bsomeone else['’]s (?:work|words|writing|essays?|text|ideas)\b|\bnot your own\b|"
    r"\b(?:" + SIMILARITY_CHECKERS + r")", re.I)
AGAINST_THE_LAW = re.compile(r"\b(?:illegal|unlawful|against the law)\b", re.I)
REAL_HARM_REASON = re.compile(
    r"\b(?:hurt\w*|danger\w*|unsafe|violen\w*|weapons?|attack\w*|crime|criminal|"
    r"harm (?:to )?(?:others|people|someone|anyone)|" + HARM_TOPICS + r")\b", re.I)
PERSON = r"(?:someone|somebody|anyone|people|a person|him|her|them|my \w+|his \w+|her \w+|their \w+|a (?:man|woman|girl|boy))"
MINOR = (r"(?:child(?:ren)?|kids?|minors?|underage|pre-?teens?|teen(?:age(?:rs?)?|s)?|school ?(?:girls?|boys?)|"
         r"\d{1,2}[- ](?:years?|yrs?)[- ]olds?)")
SEXUAL = r"(?:sex\w*|nudes?|naked|porn\w*|explicit|lewd|intimate|groom\w*)"
ILLICIT_DRUG = r"(?:methamphetamine|meth|crystal meth|crack|cocaine|heroin|fentanyl|mdma|lsd|ketamine|ghb|steroids?)"
HARD_DRUG = r"(?:methamphetamine|meth|crystal meth|crack cocaine|cocaine|heroin|fentanyl|mdma|lsd|ketamine|ghb)"
DOCTORS_NOTE = r"(?:sick|doctor['’]?s?|gp['’]?s?|fit|medical|hospital) (?:notes?|letters?|certificates?)"
FORGED = r"(?:fake[ds]?|faking|forg(?:ed|ing)|bogus|doctored|falsified|made[- ]up)"
HARMFUL_REQUEST = re.compile(r"\b(?:" + "|".join((
    HARD_DRUG,
    r"(?:synthesi[sz]\w*|cook\w*|mak(?:e|ing)|produc\w*|manufactur\w*|extract\w*|grow\w*|get|buy\w*|sell\w*|dose)\s+"
    r"(?:\w+\s+){0,3}?" + ILLICIT_DRUG,
    r"(?:synthesi[sz]\w*|cook\w*|mak(?:e|ing)|extract\w*|grow\w*)\s+(?:\w+\s+){0,3}?(?:drugs?|narcotics?|opioids?)",
    r"controlled (?:drugs?|substances?) (?:at home|without)",
    r"(?:write|writing|build\w*|mak(?:e|ing)|creat\w*|develop\w*|cod(?:e|ing)|deploy\w*|spread\w*|install\w*|plant\w*)\s+"
    r"(?:\w+\s+){0,3}?(?:malware|ransomware|viruse?s?|keylog\w*|spyware|stalkerware|botnets?|rootkits?|backdoors?|"
    r"trojans?|exploits?|worms?)",
    r"keygens?|crack\w* (?:\w+ ){0,3}?(?:passwords?|licen[cs]e\w*|activation|drm|serials?|software|wi-?fi)|"
    r"(?:working|run\w*|launch\w*|perform\w*|carry out|use|using|try\w*) (?:an? |the |some )?sql injection|"
    r"sql injection (?:attacks? |payloads? |strings? )?(?:on|against|into)|sql injection payloads?|"
    r"zero-?days? (?:exploits?|for)|exploit (?:code|kits?)",
    r"hack\w* (?:into|someone|her|his|their|my|an? (?:account|phone|computer|network))",
    r"(?:launch|run|do|start|carry out|perform)\w* (?:\w+ ){0,2}?ddos|take (?:\w+ ){0,3}?down (?:\w+ ){0,2}?"
    r"(?:site|website|server|network)s?|flood\w* (?:\w+ ){0,3}?(?:site|website|server|network)",
    r"stalk(?:ing)? " + PERSON + r"|spy(?:ing)? on|track\w* (?:\w+ ){0,2}?(?:phone|location|car|whereabouts)|"
    r"without (?:my |his |her |their |them |someone(?:['’]s)? |anyone |the (?:owner|person|victim)(?:['’]s)? )"
    r"(?:\w+ )?(?:knowing|knowledge|consent|noticing|permission)",
    SEXUAL + r"[^.?!\n]{0,60}\b" + MINOR + r"|" + MINOR + r"[^.?!\n]{0,60}\b" + SEXUAL,
    r"(?:send|messag\w*|text\w*|contact\w*|meet\w*|groom\w*|get|talk(?:ing)? to|chat(?:ting)? (?:to|with)|befriend\w*|"
    r"lure|luring|convinc\w*|persuad\w*|pick(?:ing)? up)\s+(?:\w+\s+){0,3}?" + MINOR,
    r"nudes? of|naked (?:photos?|pictures?|pics|images?|videos?) of|(?:private|intimate|explicit) (?:photos?|pictures?|"
    r"pics|images?|videos?)|revenge porn|deepfake (?:porn|nudes?)",
    r"(?:drunk|passed out|unconscious|drugged|asleep)\s+(?:\w+\s+){0,2}?(?:into bed|to have sex|to sleep with)|"
    r"spik\w* (?:\w+ ){0,2}?drinks?|date rape",
    r"forg(?:e|ing) (?:an? |my |his |her |their |the |some )?\w+|counterfeit (?:money|notes|cash|bills|goods)|"
    r"(?:make|print|produce|buy|sell|spend|pass)\w* (?:\w+ ){0,2}?counterfeit|fake (?:\w+ ){0,2}?(?:notes?|ids?|identity|"
    r"passports?|certificates?|licen[cs]es?|invoices?|receipts?|prescriptions?|signatures?|degrees?|diplomas?|documents?|"
    r"transcripts?|references?|reviews?|cheques?|money)",
    FORGED + r" (?:\w+ ){0,2}?(?:" + DOCTORS_NOTE + r"|ids?|passports?|certificates?|prescriptions?|signatures?)|"
    + DOCTORS_NOTE + r"[^.?!\n]{0,40}\b(?:fak(?:e|ed|ing)|forg(?:e|ed|ing)|made (?:it |this |that )?up)",
    r"(?:commit|run|do|pull off|carry out|get away with)\w*\s+(?:\w+\s+){0,2}?(?:fraud|scams?|money laundering)|"
    r"launder\w* (?:\w+ ){0,2}?money|(?:scam|phish|con|defraud)\w* (?:someone|people|old|elderly|my|his|her|a |an )|"
    r"scam (?:e-?mails?|messages?|texts?|letters?|calls?|sites?|websites?|pages?|scripts?)|"
    r"phishing (?:e-?mails?|messages?|texts?|pages?|sites?|kits?|campaigns?|links?)\b(?! (?:awareness|training|"
    r"detection|filters?))|(?:bank|card|account) (?:details|pins?|numbers?|logins?|passwords?)|"
    r"someone(?:['’]s| else['’]s) (?:password|account|login)",
    r"(?:threaten|blackmail|harass|intimidat|extort|dox)\w* " + PERSON + r"|threatening (?:letters?|messages?|e-?mails?|"
    r"texts?|notes?|posts?)|blackmail\w* (?:\w+ ){0,2}?(?:with|into)|dox\w*|extort\w* money|"
    r"(?:this|that|my) threats? (?:to|against|for) (?:my|his|her|their|him|them)|can(?:['’]t|not) (?:prove|tell|know|"
    r"show) (?:that )?(?:it (?:was|is|came from) me|i (?:wrote|sent|posted) (?:it|this|that|them))",
    r"steal\w* (?:from|a |an |my |someone|his |her |their |money|cars?|bikes?|phones?|cards?|data|identit\w*)|"
    r"shoplift\w*|break(?:ing)? into|burgl\w*|sell\w* (?:\w+ ){0,2}?stolen|stolen (?:\w+ ){0,2}?(?:cards?|credit|goods|"
    r"data|database|details|records|passwords?|logins?|accounts?)|pick(?:ing)? (?:the |a |my |his |her |their )?locks?|"
    r"rob(?:bing)? (?:a |an |the |my |someone|his |her |their |people)",
    r"(?:make|build|making|building|assembl\w*|3d[- ]print\w*) (?:\w+ ){0,2}?(?:bombs?|guns?|firearms?|weapons?)|"
    r"(?:buy|get|obtain)\w* (?:an? |some )?(?:guns?|firearms?|weapons?) without|"
    r"pipe bombs?|explosives?|ghost guns?|bioweapons?|toxic (?:gas|fumes)|nerve agents?|hide a body|thermite|napalm",
    r"(?:poison|kill|murder|hurt|attack|stab|shoot|kidnap|abduct)\w* " + PERSON + r"|"
    r"set(?:ting)? fire to (?:an? |the |my |his |her |their |someone)|burn(?:ing)? down (?:an? |the |my |his |her |their |"
    r"someone)|arson",
    r"self-?harm|suicid\w* (?:methods?|notes?|plans?)|kill myself|lethal (?:doses?|amounts?)|"
    r"(?:dose|doses|amount|much|many) (?:\w+ ){0,4}?(?:is |are |would be |to be )?(?:lethal|fatal|deadly)|overdose guides?",
    r"(?:plan|carry out|commit|join|fund)\w* (?:an? |the )?(?:terror\w*|mass shootings?)|shoot up|"
    r"recruit\w* (?:\w+ ){0,3}?(?:into|for|to) (?:an? |the )?(?:[\w-]+ )?(?:group|gang|cell|militia|movement)|"
    r"extremist (?:recruitment|propaganda|leaflets?|manifestos?)",
    r"(?:racist|antisemitic|homophobic|transphobic|islamophobic|sexist|bigoted|hateful) (?:\w+ )?(?:rants?|jokes?|"
    r"messages?|posts?|abuse|content|chants?|slogans?|essays?|articles?|propaganda|leaflets?)|slurs? (?:about|for|against)",
    r"revenge (?:on|against)|(?:post|share|publish|leak)\w* (?:\w+ ){0,3}?(?:home )?address|(?:his|her|their|my ex['’]?s?|"
    r"someone['’]?s?) (?:home )?address",
    r"leaked (?:\w+ ){0,2}?(?:records?|data|documents?|files?|databases?|dumps?|e-?mails?|credentials|passwords?)|"
    r"confidential (?:\w+ ){0,2}?records?|patient records?|(?:hacked|breached) (?:\w+ ){0,2}?(?:databases?|dumps?)|"
    r"(?:password|credential|customer|user) dumps?|(?:dumps?|records|e-?mails|passwords|"
    r"credentials|details) from (?:the|a|that|this) (?:\w+ )?breach|(?:link|trac|tie)\w* (?:it|this|them|that) "
    r"(?:back )?to (?:the |a |an )?(?:original |same )?(?:\w+ )?breach",
    r"defamatory|libel\w*|(?:is|was) a (?:paedophile|pedophile|rapist|murderer)",
)) + r")\b", re.I)
SAFE_HOW_TO = (r"(?:defend|protect|prevent|spot|recogni[sz]e|detect|report|stop|avoid being|stay safe|keep safe|support|"
               r"help|treat|cope|identify|solve|calculate|work out|find|prove|derive|write|structure|plan|reference|cite|"
               r"answer|revise|improve|start|conclude|introduce|integrate|differentiate|factori[sz]e|simplify|balance|draw|"
               r"label|read|learn|remember|spell|pronounce|play|use|do|make sense|understand|explain|describe|analy[sz]e|"
               r"compare|evaluate|argue|discuss|summari[sz]e|reword|rewrite|paraphrase|rephrase)\b")
INSTRUCTIONS = re.compile(
    r"\b(?:how (?:to|do i|can i|could i|would i|should i|do you|can you|would you|can one|does one|do we|can we|might i)"
    r"(?! " + SAFE_HOW_TO + r")\s+\w+|step[- ]by[- ]step|how-to (?:for|on)|"
    r"(?:practical|step|beginner['’]?s?|how-to|complete|detailed|"
    r"easy) guide|(?:best|easiest|quickest|fastest|cheapest|safest|simplest|surest|most effective) (?:ways?|methods?|means|"
    r"techniques?|places?) (?:to|of|for)|instructions (?:for|on|to)|recipe for)\b", re.I)
CONCEALMENT = re.compile(
    r"\b(?:without (?:getting|being) (?:caught|seen|noticed|traced)|get(?:ting)? away with|avoid(?:ing)? (?:the )?(?:police|"
    r"cops|detection|getting caught|being caught)|without (?:a |an )?(?:background checks?|prescriptions?|licen[cs]es?|"
    r"permits?)|undetect\w*|untrace\w*|hid(?:e|es|ing) from (?:the )?anti-?virus|evad\w* (?:the )?anti-?virus|"
    r"can(?:['’]t|not) (?:prove|trace)|without (?:them|him|her) (?:knowing|noticing))\b",
    re.I)
HARM_TOPIC = re.compile(
    r"\b(?:drugs?|narcotics?|controlled substances?|methamphetamine|meth|cocaine|heroin|fentanyl|cannabis|steroids?|"
    r"malware|ransomware|keylog\w*|spyware|stalk\w*|groom\w*|nudes?|naked|porn\w*|forg(?:e|ed|ery|ing)|counterfeit\w*|"
    r"fraud\w*|scam\w*|phish\w*|threaten\w*|intimidat\w*|blackmail\w*|harass\w*|extort\w*|theft|steal\w*|stolen|"
    r"shoplift\w*|poison\w*|explosives?|bombs?|kill\w*|murder\w*|suicid\w*|self-harm|terror\w*|weapons?|guns?|"
    r"firearms?|hack\w*|rob|robb\w*|burgl\w*|arson|lethal|fatal|overdos\w*|hurt\w*|kidnap\w*|abduct\w*|launder\w*|"
    r"bull(?:y|ying|ied)|revenge|brute[- ]?forc\w*|deauth\w*|nitroglycerine?|chloroform|napalm|thermite|molotov\w*|"
    r"ricin|gunpowder|mdma|lsd|ketamine|ghb|psilocybin|magic mushrooms?|opioids?|opiates?|sedatives?|knife|knives|"
    r"alcohol|silencers?|suppressors?|pepper spray|breathal[yi][sz]er\w*|hmrc|tax evasion|explod\w*|detonat\w*|"
    r"clon(?:e|ed|es|ing) (?:\w+ ){0,3}?cards?|card clon\w*|skimm(?:ing|ers?)|skim (?:\w+ ){0,2}?(?:cards?|atms?|"
    r"cash machines?)|deepfakes?|swat(?:s|ting|ted)?|tamper\w*|drunk|(?:web|school|content) filters?|"
    r"keystroke (?:recorders?|loggers?)|blind(?:s|ing)? (?:someone|somebody|people|him|her|them))\b", re.I)
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")
OWN_TEXT = re.compile(
    r"\b(?:my (?:own |old |earlier |first )?(?:essays?|dissertations?|thesis|reports?|coursework|assignments?|homework|"
    r"work|writing|drafts?|paragraphs?|text|words|notes|answers?|lab reports?|literature reviews?|chapters?|papers?|"
    r"articles?|sentences?)|I (?:wrote|have written|['’]ve written|drafted|have drafted))\b", re.I)
CHECKER_NAME_ONLY = (r"\bplagiari[sz]\w*(?= (?:checkers?|check(?:s|ing)?|detect\w*|tools?|software|scanners?|scores?|"
                     r"reports?|filters?|services?)\b)")
PLAIN_IDENTITY_QUESTION = re.compile(
    r"^\W*(?:(?:hey |hi )?arty,?\s*)?(?:what(?:['\u2019]s| is) your name|who are you|what are you|"
    r"are you (?:an? )?(?:ai|bot|robot|machine|person|human|real(?: person)?)"
    r"(?: or (?:an? )?(?:ai|bot|robot|machine|person|human))?|"
    r"what (?:kinds? of things |else )?can you (?:do|help(?: me)? with)|what do you do)\W*$", re.I)
DEFLECTION = re.compile(
    r"\(?[^.!?\n()]{0,200}\b(?:I keep my (?:own )?(?:instructions|set-?up|system prompt) (?:private|to myself)|"
    r"my (?:own )?instructions are (?:private|confidential))\b[^.!?\n()]{0,200}[.!?]?\)?", re.I)
DEFLECTION_REPLY_CHARS = 600
CHECKER_REQUEST_WINDOW_CHARS = 2000
REASONLESS_DECLINE = re.compile(
    r"^\W*(?:sorry,?\s+(?:but\s+)?)?I(?:['\u2019]m| am)?\s+(?:can['\u2019]?t|cannot|can not|won['\u2019]t|will not|"
    r"unable to|not able to)\s+(?:help|assist)(?:\s+you)?(?:\s+with\s+(?:that|this)(?:\s+request)?)?\s*[.!]", re.I)


def is_coursework_request(text: str) -> bool:
    return bool(COURSEWORK_REQUEST.search(text or ""))


def refuses_coursework(reply: str) -> bool:
    opening = (reply or "")[:REFUSAL_OPENING_CHARS]
    return bool(COURSEWORK_DECLINE.search(opening) and ACADEMIC_REASON.search(opening) and not HARM_REASON.search(opening))


def ends_of(text: str, window: int) -> str:
    return text if len(text) <= 2 * window else f"{text[:window]}\n{text[-window:]}"


def is_checker_rewrite_request(text: str) -> bool:
    return bool(CHECKER_REWRITE_REQUEST.search(ends_of(text or "", CHECKER_REQUEST_WINDOW_CHARS)))


def names_a_real_harm(opening: str) -> bool:
    if REAL_HARM_REASON.search(opening):
        return True
    return bool(AGAINST_THE_LAW.search(opening)) and not COPYRIGHT_REASON.search(opening)


def gives_a_real_integrity_reason(opening: str) -> bool:
    without_checker_names = re.sub(CHECKER_NAME_ONLY, "", re.sub(SIMILARITY_CHECKERS_ALONE, "", opening, flags=re.I),
                                   flags=re.I)
    return bool(INTEGRITY_REASON.search(without_checker_names))


def refuses_checker_rewrite(reply: str, own_text: bool = True) -> bool:
    opening = (reply or "")[:REFUSAL_OPENING_CHARS]
    reason_given = INTEGRITY_REASON.search(opening) if own_text else gives_a_real_integrity_reason(opening)
    return bool(COURSEWORK_DECLINE.search(opening) and reason_given and not names_a_real_harm(opening))


def names_a_harmful_request(text: str) -> bool:
    text = text or ""
    return bool(safety.check(text) or HARMFUL_REQUEST.search(text))


def asks_for_harmful_steps(text: str) -> bool:
    return any((INSTRUCTIONS.search(sentence) or CONCEALMENT.search(sentence)) and HARM_TOPIC.search(sentence)
               for sentence in SENTENCE_BREAK.split(text or ""))


def asks_for_harm(request: str) -> bool:
    return names_a_harmful_request(request) or asks_for_harmful_steps(request)


def rewords_to_hide_the_author(text: str) -> bool:
    return bool(REWORDING_REQUEST.search(text) and AUTHOR_HIDING.search(text))


def asks_to_disguise_harm(request: str) -> bool:
    request = request or ""
    if not (is_checker_rewrite_request(request) or rewords_to_hide_the_author(request)):
        return False
    return asks_for_harm(request) or (rewords_to_hide_the_author(request) and bool(THREAT_TEXT.search(request)))


def declines_without_a_reason(reply: str) -> bool:
    opening = (reply or "")[:REFUSAL_OPENING_CHARS]
    return (bool(REASONLESS_DECLINE.match(opening)) and not HARM_REASON.search(opening)
            and not names_a_real_harm(opening))


def is_own_text(request: str) -> bool:
    return bool(OWN_TEXT.search(request or ""))


def is_needless_coursework_refusal(request: str, reply: str, earlier_requests=()) -> bool:
    if asks_to_disguise_harm(request) or any(asks_for_harm(text) for text in (request, *earlier_requests)):
        return False
    if is_coursework_request(request) and declines_without_a_reason(reply):
        return True
    if is_checker_rewrite_request(request):
        return refuses_checker_rewrite(reply, is_own_text(request))
    return is_coursework_request(request) and refuses_coursework(reply)


def is_plain_identity_question(text: str) -> bool:
    return bool(PLAIN_IDENTITY_QUESTION.match((text or "").strip()))


def has_a_deflection(reply: str) -> bool:
    return len(reply or "") <= DEFLECTION_REPLY_CHARS and bool(DEFLECTION.search(reply or ""))


def without_deflection(reply: str) -> str:
    if not has_a_deflection(reply):
        return (reply or "").strip()
    return re.sub(r"[ \t]{2,}", " ", DEFLECTION.sub("", reply)).strip()


def is_only_a_deflection(reply: str) -> bool:
    return has_a_deflection(reply) and not without_trailing_offers(without_deflection(reply))


# A reply that plays at using tools (made-up JSON results, "using the `homelab__x` function") instead of answering.
PRETEND_TOOLS = re.compile(r"```json|\b[a-z]+__[a-z_]+\b|<tool_call>", re.I)
# Sentences that only talk about Arty's own machinery.
TOOL_META_SENTENCE = re.compile(r"[^.!?\n]*(\b(none of the|the provided|the available|my available)\s+(functions|tools)\b|"
                                r"\b[a-z]+__[a-z_]+\b|answer (your question )?directly instead|(with|using) the (tool|function)\b)"
                                r"[^.!?\n]*[.!?:]?", re.I)
# A tool result that says it didn't work: the start of hub.call's errors, MCP errors and native refusals.
TOOL_FAILED = re.compile(r"^\W*(unknown tool|tool error|tool unavailable|error\b|failed\b|blocked\b|(i )?(couldn't|could not|can't|cannot)\b)", re.I)
ANY_TEXT = "{any}"
CLIENT_REFUSAL_TEXTS = (
    "The user didn't allow that, so it wasn't done. Don't try another way round it; say so and ask what they'd like.",
    "The user didn't allow that, so it wasn't done. They said what to do instead: {any}. Do that, and don't try another "
    "way round what they refused.",
    "Plan mode is on: nothing may be changed or run except looking. Finish looking, then set out the plan step by step "
    "and ask the user to switch plan mode off (/mode default) to carry it out.",
    "Plan mode is on: nothing may be changed or run except looking. Finish looking, then set out the plan step by step "
    "with present_plan so the user can approve it and switch plan mode off.",
    "A permission rule in the settings forbids this, so it wasn't done. Don't try another way round it; tell the user.",
    "A permission rule in the settings forbids some commands, and this command hides which programs it runs from that "
    "rule ({any}), so it wasn't run. If it is allowed, write it again as plain words without those; don't try another "
    "way round the rule.",
)


def refusal_pattern(text: str) -> str:
    return re.escape(text).replace(re.escape(ANY_TEXT), ".+").replace("'", "['’]")


CLIENT_REFUSED = re.compile("|".join(map(refusal_pattern, CLIENT_REFUSAL_TEXTS)), re.I | re.DOTALL)


def is_client_refusal(result: str) -> bool:
    return bool(CLIENT_REFUSED.fullmatch(result.strip()))
PRIVATE_CALL_NOT_RUN = ("Not run: this call held part of Arty's own private instructions, and they are never written, sent "
                        "or run. Nothing was done. Make the call again without that text.")
SIBLING_CALL_NOT_RUN = ("Not run: another call in the same step held part of Arty's own private instructions, so nothing in "
                        "that step was done. Make this call again if it is still needed.")
HELD_CALL_RESULTS = (PRIVATE_CALL_NOT_RUN, SIBLING_CALL_NOT_RUN)
FAILED_NOTE = ("\n\n(That didn't work. Tell the user plainly that it failed and why, in a sentence; never say it worked "
               "and never make up a result.)")
# Tools that do things; read-only lookups and unlocking tool groups don't count as having done anything.
NOT_ACTIONS = {"use_tools"}
LOOK_ONLY_TOOLS = {"chrome_tabs", "chrome_read", "chrome_find", "chrome_scroll", "chrome_look", "computer_look", "computer_apps",
                   "computer_read_file", "computer_list_files", "computer_find_files", "computer_search", "read_clipboard",
                   "desktop_windows", "desktop_notifications", "system_status", "code_sandbox"}
NOT_FOR_ME = "(not for me)"
VOICE_MODE = "(voice mode)"
ON_A_PHONE_CALL = "on a voice call with you"
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
NOTE_CLIENTS = frozenset({"desktop", "phone", "android"})
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

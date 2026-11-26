import bisect
import datetime
import functools
import json
import re
import unicodedata

ADULT_AGE = 18
CONTEXT_CHARS = 200000
RECENT_CHARS = 2000
NEARBY_CHARS = 200
ACT_NEARBY_CHARS = 1000
LONG_TOKEN_CHARS = 200
MAX_UNBROKEN_CHARS = 400
OWN_FAMILY_CHARS = 25
CREATIVE_NEARBY_CHARS = 1500
INSTRUCTION_CHARS = 400
CODE_LINE_SHARE = 0.4
CODE_LINES_NEEDED = 5
TEEN_ADULT_CHARS = 12
FAMILY_LOOK_BACK_CHARS = 40
FAMILY_LOOK_AHEAD_CHARS = 30
LONGEST_MATCH_CHARS = 120
OVERLAP_CHARS = ACT_NEARBY_CHARS + LONGEST_MATCH_CHARS
CHECK_EVERY_CHARS = 64
MINORS_REPLY = "I won't write anything sexual involving anyone under 18."
CANT_CHECK_REPLY = "I can't check this request right now. Try again in a moment."

DASH_LIKE = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"
LOOK_ALIKES = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i",
    "ј": "j", "ѕ": "s", "ԁ": "d", "һ": "h", "А": "A", "В": "B", "Е": "E", "К": "K",
    "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X",
    "Ѕ": "S", "І": "I", "ο": "o", "α": "a", "ε": "e", "ι": "i", "κ": "k", "ν": "v",
    "ρ": "p", "τ": "t", "υ": "u", "χ": "x", "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z",
    "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T",
    "Υ": "Y", "Χ": "X",
}
WORD_DOTS = "\u00b7\u2022\u2027\u2219\u30fb"
PLAIN_LETTERS = str.maketrans({**dict.fromkeys(DASH_LIKE, "-"), **dict.fromkeys(WORD_DOTS, " "), **LOOK_ALIKES})
DROPPED_CATEGORIES = frozenset({"Cf", "Mn", "Me"})

UNITS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}
TEENS = {"ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
         "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
SPELLED = (r"(?:(?:" + "|".join(TENS) + r")(?:[\s-](?:" + "|".join(UNITS) + r"))?|" + "|".join(TEENS) + "|"
           + "|".join(UNITS) + r")")
AGE = (r"(?<![\w.,:/-])(\d{1,3}(?:[.,]\d{1,2})?(?!\d|[.,:]\d)|" + SPELLED + r"(?:\s+and\s+an?\s+half)?(?![a-z]))")
AGE_END = (r"(?=\s*(?:$|[,.;:!?)\]]|-?\s*(?:years?|yrs?|y/?o)\b|"
           r"\s(?:and|but|so|now|today|tonight|soon|when|then|who|herself|himself)\b))")
PEOPLE = (r"(?:woman|women|man|men|girls?|boys?|guy|lady|lad|wife|husband|girlfriend|boyfriend|partner|students?|pupils?|"
          r"neighbou?r|teacher|nurse|maid|stranger|character|daughter|son|niece|nephew|sister|brother|cousin|"
          r"stepdaughter|stepson|kid|child|teen|teenager|person|victim)")
SUBJECTS = r"(?:she|he|i|you|they|we|her|him|who|that)"
NOT_NAMES = (r"(?!(?:Today|Tomorrow|Tonight|It|This|That|There|Here|Number|Chapter|Page|Score|Total|Rank|Level|Version|Size|"
             r"Price|Time|Day|Room|Floor|Part|Step|Round|Item|Answer|Result|Question|Rule|Table|Line|Year|Grade|Class|Box|"
             r"Mine|Yours|What|Which|Who|How|When|Where|Why|And|But|So|The|If|Is|Are|Was|She|He|They|We|You)\b)")
NAME = r"(?-i:" + NOT_NAMES + r"[A-Z][a-z]{1,20})"
QUALIFIERS = r"(?:(?:only|just|now|barely|about|around|nearly|almost|still|really|actually)\s+)*"
AGE_TAIL = r"\s*(?:yrs?|years?(?:\s+old)?)?\s*(?=[,).;:]|$|\s+(?:and|is|was|who)\b)"
AGE_ASSIGNMENTS = (
    r"\b(?:younger|de-?aged?|age[ds]?\s+(?:her|him|them|it)\s+down)\b[^.!?\n]{0,25}?\b(?:like|say|about|around|maybe|to|at)\s+"
    r"(?:(?:only|just)\s+)?(\d|1[0-7]|" + "|".join(list(UNITS) + [word for word, value in TEENS.items() if value < 18]) + r")\b",
    r"\b(?:set|sets|change|changes|changed|make|makes|made|put|puts)\s+(?:her|his|their|its|the(?:\s+\w+)?)\s+age\s+(?:to|at|as)\s+"
    r"(\d|1[0-7]|" + "|".join(list(UNITS) + [word for word, value in TEENS.items() if value < 18]) + r")\b",
)
LOWER_NAMED_AGE = (r"(?:^|[.!?\n]\s*|\b(?:named|called)\s+)(?-i:(?!(?:she|he|i|you|they|we|it|this|that|there|here|who|what|"
                   r"the|a|an|my|our|your|her|his|age|time|score|total)\b)([a-z]{2,20}))\s+(?:is|was|turned|turns|aged)\s+"
                   + QUALIFIERS + AGE + AGE_END)
NAMED_AGES = (
    r"\b(" + NAME + r")\s+(?:is|was|turned|turns|becomes|became|aged)\s+" + QUALIFIERS + AGE + AGE_END,
    r"\b(" + NAME + r")'s\s+" + QUALIFIERS + AGE + AGE_END,
    r"\b(" + NAME + r")\s*[,(]\s*(?:aged?\s+)?" + AGE + AGE_TAIL,
    r"\bname\s*:\s*(" + NAME + r")\b[^\n]*\n(?:[^\n]*\n){0,4}?[ \t]*age\s*[:=]\s*" + AGE + AGE_TAIL,
)
AGE_STATEMENTS = (
    AGE + r"[\s-]*(?:years?|yrs?)[\s-]*(?:olds?|of age)\b",
    AGE + r"\s*-?\s*(?:yo|y/o|y\.o\.?)(?!\w)",
    r"\b(?:aged?|age of|age\s*[:=?]+|age is)\s*" + AGE,
    r"\b" + SUBJECTS + r"(?:'s|'m|'re|\s+(?:is|am|are|was|were|turned|turns|just turned))\s+" + QUALIFIERS + AGE + AGE_END,
    r"\b(?:make|making|made|makes)\s+(?:her|him|them|me|you|" + NAME + r")\s+" + AGE + AGE_END,
    r"\b(?:are|is)\s+(?:(?:now|both|all)\s+)+" + AGE + AGE_END,
    r"\b" + PEOPLE + r"\s*[,(]\s*" + AGE + AGE_TAIL,
    r"\b" + PEOPLE + r"\s+(?:is|was|turned|just turned)\s+" + QUALIFIERS + AGE + AGE_END,
    r"\b" + PEOPLE + r"\s+of\s+" + AGE + AGE_END,
    r"\b" + PEOPLE + r"\s+(?:in|from|at|on)\s+(?:\w+\s+){1,3}?(?:is|was)\s+" + QUALIFIERS + AGE + AGE_END,
    r"\b(?:looks?|looked|looking|seems?|seemed|appears?|appeared|appearing|passes for|passing for|physically|biologically|"
    r"bodily|in body)\s+(?:to be\s+)?(?:only\s+|about\s+|around\s+|like\s+|maybe\s+)?" + AGE + AGE_END,
    *AGE_ASSIGNMENTS,
    AGE + r"\s*(?:anos|ans|jahre|anni|jaar)\b",
    r"\b(?:of|seen)\s+" + AGE + r"\s+(?:summers|winters|springs|christmases|birthdays)\b",
    r"\bborn\s+" + AGE + r"\s+years?\s+ago\b",
    LOWER_NAMED_AGE,
    *NAMED_AGES,
)
AGE_HINT = re.compile(r"\d|\b(?:" + "|".join([*UNITS, *TEENS, *TENS]) + r"|first|second|third|fourth|fifth|sixth|seventh|"
                      r"adult|minderjahrig\w*|menor(?:es)?)\b", re.IGNORECASE)
AGE_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in AGE_STATEMENTS)
NAMED_AGE_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (*NAMED_AGES, LOWER_NAMED_AGE))
AGE_ASSIGNMENT_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in AGE_ASSIGNMENTS)
BIRTHDAY = re.compile(r"\b(?:her|his|their|my|your)\s+(\d{1,2})(?:st|nd|rd|th)\s+birthday\b", re.IGNORECASE)
BORN_IN = re.compile(r"\bborn\s+(?:in\s+|on\s+)?(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+"
                     r"(?:\d{1,2}(?:st|nd|rd|th)?,?\s+)?)?((?:19|20)\d\d|'\d\d)\b", re.IGNORECASE)
CENTURY = 100
SUMMED_AGE = re.compile(r"\b(?:is|was|are|aged?)\s+" + AGE + r"\s+plus\s+" + AGE + AGE_END, re.IGNORECASE)
HALF_MY_AGE = re.compile(r"\bhalf (my|our|his|her) age\b", re.IGNORECASE)
OWNER_AGES = {
    owner: re.compile(r"\b" + subject + r"\s+" + QUALIFIERS + AGE + AGE_END, re.IGNORECASE)
    for owner, subject in (("my", r"i(?:'m|\s+am)"), ("our", r"we(?:'re|\s+are)"), ("his", r"he(?:'s|\s+is)"),
                           ("her", r"she(?:'s|\s+is)"))
}
YEARS_SHORT_HINT = re.compile(r"\byears?\s+(?:short|shy|away|off|before|from)\b", re.IGNORECASE)
YEARS_SHORT = re.compile(r"\b" + AGE + r"\s+years?\s+(?:short of|shy of|away from|off|before|from)\s+" + AGE + AGE_END,
                         re.IGNORECASE)
OTHER_PERSON = re.compile(r"\b(?:whose|his|her|their|" + PEOPLE[3:-1] + r")\b", re.IGNORECASE)
ALMOST = re.compile(r"\b(?:almost|nearly|not yet|under)\b", re.IGNORECASE)
UNDER_ADULT = re.compile(
    r"\b(?:almost|nearly|not yet|not quite|under(?:\s+the\s+age\s+of)?|below|younger than|less than|just under|barely under)\s+"
    r"(?:18|eighteen)\b(?!\s*\+)|\bnot\s+(?:18|eighteen)\s+yet\b|\bnot\s+(?:yet\s+)?(?:an\s+)?adult\b|"
    r"\bminderjahrig\w*|\bmenor(?:es)?\s+de\s+edad\b",
    re.IGNORECASE)

SCHOOL_YEAR_WORDS = r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen)"
YOUNG_WORDS = re.compile(
    r"\b(?:child|children|childlike|child-like|kids?|kiddies?|kiddos?|toddlers?|infants?|newborns?|preschoolers?|"
    r"kindergart(?:e|n)ers?|pre-?teens?|tweens?|under-?aged?|loli(?:s|con|ta)?|shota(?:s|con)?|jailbait|pre-?pubescent|"
    r"pubescent|adolescents?|juveniles?|youngsters?|school ?(?:girls?|boys?|child(?:ren)?|kids?)|schoolchild(?:ren)?|"
    r"little[\s-](?:girls?|boys?)|young[\s-](?:girls?|boys?)|p(?:a|ae|e)dophil\w*|p(?:a|ae|e)dos?|"
    r"(?:primary|middle|elementary|junior high|high|grade|secondary|prep)[\s-]?school(?:er)?s?|"
    r"(?:high[\s-]?school\s+)?(?:freshm[ae]n|sophomores?)|in (?:year|grade)[\s-]+(?:[1-9]|1[0-3]|" + SCHOOL_YEAR_WORDS + r")"
    r"(?!\s+of\s+(?!(?:(?:primary|secondary|middle|junior|high|elementary|grade)\s+)?school\b))|"
    r"year[\s-]+(?:[1-9]|1[0-3]|" + SCHOOL_YEAR_WORDS + r")[\s-]+(?:pupils?|students?|girls?|boys?|kids?|child(?:ren)?|"
    r"lass(?:es|ies)?|lassie|lads?)|"
    r"(?:[1-9]|1[0-2])(?:st|nd|rd|th)[\s-]+grade(?:rs?)?|grade[\s-]+(?:[1-9]|1[0-2])(?:rs?)?|"
    r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth)[\s-]+grade(?:rs?)?|"
    r"(?-i:nin[ao]s?)|colegialas?|nenas?|enfants?|fillettes?|petites? filles?|gamines?|mineures?|collegiennes?|madchen|"
    r"schulerin(?:nen)?|menin[ao]s?|criancas?|bambin[ao]|ragazzin[ao]|minorenni?)\b",
    re.IGNORECASE)
NOUN_MINOR = re.compile(
    r"\b(?:a|the|this|that|these|those|young|underage)\s+minors?\b(?=\s*(?:$|[,.;:!?)]|'s\b|next\b|who\b|that\b|and\b|or\b|"
    r"in\b|on\b|at\b|from\b|with\b|is\b|was\b|has\b|had\b|to\b|for\b|of\b))|\bminors\b(?!\s+(?:league|chords?|keys?|scales?)\b)",
    re.IGNORECASE)
TEEN_WORDS = re.compile(r"\b(?:teens?|teenagers?|teenaged?|teeny)\b", re.IGNORECASE)
COLLEGE_YEARS = re.compile(r"\b(?:college|university|uni)\b", re.IGNORECASE)
SCHOOL_YEAR_NAMES = re.compile(r"freshm|sophomore|^in (?:year|grade)\b", re.IGNORECASE)
NOT_A_PERSON = re.compile(
    r"\bchild(?:ren)?[\s-]+(?:process(?:es)?|nodes?|elements?|class(?:es)?|themes?|components?|widgets?|windows?|tables?|"
    r"rows?|records?|tasks?|threads?|pids?|director(?:y|ies)|folders?|objects?|items?|views?|tags?|entit(?:y|ies)|modules?|"
    r"keys?|spans?|frames?|accounts?|issues?|tickets?|pages?|layers?|scopes?|commits?|branches?|categor(?:y|ies)|"
    r"selectors?|routes?|jobs?|workers?|resources?|states?|props?|lists?)\b|\bkid gloves\b|"
    r"\b(?:high|secondary|grade)[\s-]?school (?:reunions?|sweethearts?|friends?|days|years|crush(?:es)?)\b|"
    r"\bschool ?(?:girl|boy) (?:costumes?|outfits?)\b|\bdressed (?:up )?as an? school ?(?:girl|boy)\b|"
    r"\b(?:grade|year) \d+ (?:bolts?|steel|maths?|exam|essay|piano)\b",
    re.IGNORECASE)
FAMILY = re.compile(
    r"\b(?:the\s+(?:kids?|children|child|baby|babies)|(?:my|our|your|his|her|their)\s+(?:own\s+)?(?:(?:teenage|teen|young|little|grown|eldest|youngest|two|three|four|"
    r"\d+)\s+)?(?:kids?|children|child|sons?|daughters?|toddlers?|babies|baby|teens?|teenagers?|little (?:girl|boy)s?|"
    r"stepdaughters?|stepsons?|nieces?|nephews?)"
    r"|(?:has|have|had|raising)\s+(?:a|an|\d+|some|no|two|three|four|five|grown)\s+(?:kids?|children|child)"
    r"|(?:mum|mom|mother|dad|father|parent)s?\s+(?:of|to)\s+(?:a|an|\d+|two|three|four|five)\s+(?:kids?|children|child))\b",
    re.IGNORECASE)

SEX_OBFUSCATED = r"s(?:[.*_-]?[e3*][.*_-]?|\s[e3]\s)x"
TOPIC_TERMS = (
    r"sex(?:ual(?:ly|i[sz]ed|i[sz]ing|i[sz]es?|ity)?|y|ier|iest|ting|ted|ts|xx*)?|" + SEX_OBFUSCATED + r"|erotic\w*|porn\w*|pr[o0]n|n[o0]{2}d[sz]|nsfw|"
    r"s[e3](?:cks|ks|ggs|gs)|smex|penetrat\w*|climax(?:es|ed|ing)?|"
    r"oral(?:ly)?(?!\s+(?:exams?|history|hygiene|health|surgery|traditions?|presentations?|tests?|care|medications?|thrush|"
    r"cancer|reports?|arguments?|stages?|vaccines?|contraceptives?|english|french|spanish|german|skills?|questions?|"
    r"examinations?|cavity|cavities|exam))|"
    r"sexo|sexuales?|sexuel(?:le)?s?|sexuell\w*|sesso|desnud[oa]s?|nackt\w*|nues?|erotique|erotisch\w*|follar|ficken|"
    r"scopare|transar|baiser avec|"
    r"lewd\w*|smut\w*|hentai|intercourse|rap(?:e|es|ed|ing|ist|ists)|molest\w*|naked(?:ness)?|nud(?:e|es|ity|ist|ists|ism)|"
    r"orgasm\w*|horny|arous(?:ed|al|ing)|masturbat\w*|erections?|hard-?on|"
    r"undress\w*|unclothed|seduc\w*|lingerie|panties|prostitut\w*|onlyfans|incest\w*|fetish\w*|kinky|bdsm|"
    r"grop\w*|fondl\w*|strip(?:s|ped|ping)?\s+(?:naked|nude|for (?:me|him|her|you|them))|clothes off|sexting|"
    r"mak(?:e|es|ing)\s+out\s+with|stripper|striptease|virginity|deflower\w*|"
    r"(?:takes?|took|taking|pull(?:s|ed|ing)?|tear(?:s|ing)?|tore|rip(?:s|ped|ping)?|strip(?:s|ped|ping)?)\s+(?:off\s+)?"
    r"(?:her|his)\s+(?:clothes|top|shirt|dress|pants|panties|knickers|underwear|bra|skirt)"
)
ACT_TERMS = (
    r"blow-?jobs?|hand-?jobs?|fellatio|cunnilingus|cumm?ing|cumshots?|"
    r"(?<!summa )(?<!magna )(?<![-\w])cum(?![-\w])(?!\s+laude)|pussy|clits?|"
    r"undress(?:es|ed|ing)?\s+(?:her|him|me|them)|"
    r"(?:her|his|their)\s+naked\s+(?:body|bodies|skin|chest|breasts)|naked\s+(?:body|bodies)\s+(?:pressed|against)|"
    r"(?:lay|lying|lies)\s+naked\s+(?:with|beside|next to)|strip(?:s|ped|ping)?\s+(?:her|him)\s+naked|"
    r"fuck(?:s|ed|ing)?\s+(?:her|him|them)|(?:he|she|they|we|i)\s+fuck(?:s|ed)?\s+(?-i:[A-Z][a-z]+)\b(?!\s+(?:off|up|over)\b)|shag(?:s|ged|ging)?|"
    r"(?:her|his)\s+(?:cock|dick|tits|boobs|pussy|crotch)|spread(?:s|ing)?\s+(?:her|his)\s+legs|"
    r"(?:licks?|licked|licking|sucks?|sucked|sucking)\s+(?:her|his)\s+(?:nipples?|breasts?|cock|dick|pussy|clit|tits)|"
    r"moan(?:s|ed|ing)?\s+(?:with|in)\s+(?:pleasure|ecstasy|desire)|thrust(?:s|ed|ing)?\s+(?:into|inside)|"
    r"(?:came|cums?)\s+(?:inside|all over)|ma(?:k|d)(?:e|es|ing)\s+love|"
    r"penetrat(?:es|ed|ing)\s+(?:her|him|them|me|you)|(?:she|he|they|i|we)\s+climax(?:es|ed)?|climaxed|"
    r"(?:had|has|have|having)\s+(?:oral\s+|anal\s+)?sex\s+with\s+(?:her|him|them|me|you)"
    r"(?!\s+(?:own\s+|new\s+|older\s+|first\s+)?(?:boy|girl|husband|wife|partner|fianc|friend|mum|mom|dad|lover|date)\w*)"
)
CREATIVE_ONLY_TERMS = (r"explicit|intimate|steamy|spicy|sensual|x-rated|r-rated|18\+|"
                       r"naughty (?:stor(?:y|ies)|scenes?|role-?plays?|rp|pics?|photos?|stuff|things|bits|games?)|"
                       r"adult (?:story|scene|content|roleplay|rp|version)|"
                       r"make (?:it|this|her|him) (?:hot|hotter|sexier|spicier|steamier|dirtier|naughty|naughtier)")
WEAK_TERMS = (r"breasts?|nipples?|penis|vagina|vulva|clitoris|genitals?|genitalia|dick|cock|tits|boobs|moan\w*|thongs?|kiss\w*|"
              r"touch\w*|fuck\w*|lust\w*|romanc\w*|romantic|anal|massag\w*|underwear|knickers|bikini|private parts|"
              r"grooming|spank\w*|tickl\w*|lick\w*|cuddl\w*|naughty|bedroom|in bed|"
              r"mak(?:e|es|ing)\s+out(?!\s+(?:of|the|a|an|what|that|if|whether|to|like|as|his|her|their|its|my|your|with)\b)")
TOPIC = re.compile(r"\b(?:" + TOPIC_TERMS + r"|" + ACT_TERMS + r")(?![\w+])", re.IGNORECASE)
ACT = re.compile(r"\b(?:" + ACT_TERMS + r")(?![\w+])", re.IGNORECASE)
CREATIVE_ONLY = re.compile(r"\b(?:" + CREATIVE_ONLY_TERMS + r")(?![\w+])", re.IGNORECASE)
TERM_START = r"\b(?=\w)(?![a-hj-rt-z]\s)"
ANY_SEXUAL = re.compile(TERM_START + r"(?:" + TOPIC_TERMS + r"|" + ACT_TERMS + r"|" + CREATIVE_ONLY_TERMS + r"|" + WEAK_TERMS + r")(?![\w+])",
                        re.IGNORECASE)
STRONG_SEXUAL = re.compile(TERM_START + r"(?:" + TOPIC_TERMS + r"|" + ACT_TERMS + r"|" + CREATIVE_ONLY_TERMS + r")(?![\w+])",
                           re.IGNORECASE)
ALWAYS_SAFE_PHRASES = re.compile(
    r"\b(?:sex(?:ual)? (?:education|ed|health)|safe sex|sexual orientation|same-?sex|opposite sex|biological sex|"
    r"sex (?:of|determination of) (?:the|a|my|your|our|their) (?:baby|babies|child|foetus|fetus|puppy|kitten|chick)|"
    r"sex chromosomes?|age of consent|body safety|sex(?:ual)? (?:health )?clinics?|(?:sex|gender) assigned at birth|"
    r"(?:run|runs|running|ran|play(?:s|ing|ed)?|swim(?:s|ming)?|bath(?:e|es|ing)?|splash(?:es|ing)?)\s+(?:around\s+)?"
    r"(?:naked|nude)|naked (?:eye|truth|flames?|wires?|ambition|mole[\s-]rats?|singularity|lunch)|"
    r"nude (?:colou?rs?|lipsticks?|shades?|tights|heels)|(?:naked|nude) new-?borns?|"
    r"(?:mother|mum|mom|mommy|mummy|mama)'?s? breasts?|breast-?(?:feed\w*|fed|milk|pump\w*)|at the breast|"
    r"reproduc\w* (?:a)?sexually|(?:a)?sexual(?:ly)? reproduc\w*|asexual\w*|"
    r"seduc\w* (?:an? |the |their |his |her )?(?:audiences?|crowds?|readers?|markets?|voters?|customers?|investors?|jury|judges?|"
    r"crows?|ravens?|magpies?|birds?|hens?|chickens?|ducks?|geese|mice|rats?|rabbits?|hares?|cats?|dogs?|wolves|foxes|sheep|"
    r"lambs?|bees?|fish|frogs?|squirrels?|animals?))\b",
    re.IGNORECASE)
BODY_WORDS = (r"(?:body|bodies|skin|face|cheeks?|lips?|mouth|neck|chest|breasts?|nipples?|belly|tummy|stomach|waist|hips?|thighs?|"
              r"legs?|knees?|bum|bottom|butt|backside|crotch|groin|privates?|private|parts?|genitals?|hair|arms?|shoulders?|back|"
              r"feet|foot|toes|hands?|fingers?|inner|bare|naked|little|soft|small|tiny|young|smooth|warm|underwear|knickers|"
              r"panties|pants|bra|dress|skirt|shirt|top|blouse|nightie|pyjamas?|pajamas?|clothes|self|herself|himself|yourself)")
NOT_A_THING = (r"(?![a-z]*ly\b)(?!(?:" + BODY_WORDS + r"|and|or|while|as|until|again|there|down|up|all|everywhere|over|under|between|"
               r"inside|through|where|in|on|me|you|him|her|them|us|each|one|anyone|someone)\b)")
INNOCENT_USES = re.compile(
    r"\btouch(?:es|ed|ing)?\s+(?:nothing\b|(?:the|a|an|her|his|their|my|your|its|some|wet|that|this|those|these)\s+"
    + NOT_A_THING + r"[a-z]{3,}\b)|\btouched\s+by\b|"
    r"\blick(?:s|ed|ing)?\s+(?:the|a|an|her|his|their|my|your)\s+(?:spoon|bowl|lolly|lollipop|ice[\s-]?cream|icing|batter|plate|"
    r"stamps?|envelopes?|cones?|fingers? clean|paws?)\b|\bmoan(?:s|ed|ing)?\s+(?:about|that)\b|"
    r"\btuck(?:s|ed|ing)?\s+(?:\w+\s+){1,3}?in(?:to)?\s+bed\b|\b(?:ill|sick|poorly|unwell)\s+in\s+bed\b|"
    r"\b(?:covered|full)\s+(?:in|of|with)\s+kisses\b|\b(?:hugs|love)\s+and\s+kisses\b|"
    r"\b(?:for\s+)?being\s+naughty\b|\bnaughty\s+(?:step|list|corner|chair|spot)\b|"
    r"\btickl(?:e|es|ed|ing)\s+(?:her|his|their|my|your|the|its)\s+(?:toes|feet|foot)\b|"
    r"\bbikini[\s-]*clad\s+(?:doll|barbie|figure|toy)\b|\b(?:doll|barbie)'?s?\s+bikini\b",
    re.IGNORECASE)
OWN_FAMILY_CARE = re.compile(
    r"\b(?:my|our)\s+(?:[\w'-]+\s+){0,3}?(?:sons?|daughters?|kids?|children|child|bab(?:y|ies)|newborns?|toddlers?|nieces?|"
    r"nephews?|grand(?:sons?|daughters?|kids?|children|child))\b|\b(?:mum|mom|mummy|mommy|mother|dad|daddy|father|gran|granny|"
    r"grandma|grandmother|grandad|granddad|grandpa|grandfather|nan|nana|nanny|parents?|big (?:sister|brother))\b|\bnewborns?\b",
    re.IGNORECASE)
PARENT_ROMANCE = re.compile(
    r"\b(?:her|his|my|their|the|whose)\s+(?:mum|mom|mother|dad|father|parents?|gran|grandma|grandad|grandpa)\s+(?:\w+\s+){0,2}?"
    r"(?:dat(?:e|es|ed|ing)|(?:is|are|was|were|falls?|fell)\s+(?:\w+\s+)?(?:in love|for)|romanc\w*|remarr\w*)\b", re.IGNORECASE)
PEER_ROMANCE = re.compile(
    r"\btwo\s+(?:\d{1,2}|" + SPELLED + r")[\s-]+years?[\s-]+olds\b|\b(?:two|young)\s+(?:teens|teenagers|classmates|pupils|"
    r"students)\b", re.IGNORECASE)
INNOCENT_TERMS = re.compile(r"kiss\w*|cuddl\w*|tickl\w*|romanc\w*|romantic|massag\w*|bikini", re.IGNORECASE)
ROMANCE_TERMS = re.compile(r"\b(?:romanc\w*|romantic|love stor(?:y|ies)|in love|falls? for|fell for|date|dating)\b", re.IGNORECASE)
PRIVATE_SETTING = re.compile(
    r"\b(?:my|your|our)\s+(?:bed|bedroom|room|lap|tent|car)\b|\bour (?:little )?secret\b|"
    r"\bin(?:to)? (?:the |my |your |his |her )?bed with (?:you|me|her|him)\b|"
    r"\b(?:don't|do not|never|won't|mustn't) tell (?:your|anyone|any one|mum|mom|dad|mummy|mommy|daddy)\b|"
    r"\balone (?:with|at home|in the house)\b|\bat home alone\b|\bunder the (?:blankets?|covers|sheets|duvet)\b|"
    r"\block(?:s|ed)? the door\b(?!\s+(?:of|to)\s+(?:her|his|their)\s+(?:own\s+)?(?:bed)?room\b)|\bbehind closed doors\b|\bafter class\b|\bdetention\b|\bnight(?:ie|dress|gown)s?\b|"
    r"\b(?:i|we)\s+(?:\w+\s+){0,3}?(?:give|kiss|touch|cuddl|strok|hold|undress|lick|massag|tickl|pull|slid|spank|make out)\w*\b",
    re.IGNORECASE)
CLOSE_CONTACT = r"\b(?:on|onto|in)\s+my\s+lap\b"
GROOMING_ACTS = re.compile(
    r"\b(?:hand|hands|fingers)\s+(?:under|up|inside|down)\s+(?:your|her|his)\b|"
    + CLOSE_CONTACT + r"|\b(?:come|get|climb|crawl)\s+(?:in|into|to)\s+my\s+bed\b|"
    r"\btak(?:e|es|ing)\s+off\s+(?:her|his|your)\s+(?:dress|clothes|top|shirt|skirt|nightie|pyjamas|pajamas|knickers|"
    r"underwear|pants)\b|\bundress(?:es|ed|ing)?\s+(?:for|in front of)\s+(?:me|him|you|them)\b|\bour (?:little )?secret\b|"
    r"\b(?:don't|do not|never|mustn't) tell (?:your|anyone|mum|mom|dad|mummy|mommy|daddy)\b",
    re.IGNORECASE)
FAMILY_CONTACT = re.compile(CLOSE_CONTACT, re.IGNORECASE)
PARTNER_SETTING = re.compile(r"\balone\b|\bat home\b|\bin bed\b|\bon the (?:sofa|couch|settee)\b|\bin (?:his|her|the|their) (?:car|bedroom|room|office|flat|house)\b",
                             re.IGNORECASE)
THEN = (r"(?:back then|then|at the time|in those days|that time|(?:her|his|their) (?:school ?days|school years|childhood|youth|teens|"
        r"teenage years)|when (?:she|he|they) (?:was|were)(?: \w+){0,3})")
SET_BACK_THEN = re.compile(
    r"\b(?:flash(?:es|ing)?[\s-]?backs?|as a (?:girl|boy|child|schoolgirl|schoolboy|teen)|"
    r"(?:set|takes? place|taking place|happens?|happening)\s+(?:\w+\s+){0,2}?(?:in\s+|during\s+|back\s+)?" + THEN + r"|"
    r"(?:scenes?|story|stories|chapters?)\s+(?:from|of|in|about|during)\s+" + THEN + r"|"
    r"(?:go|goes|going|went)\s+back\s+to\s+(?:then|when|those days|that time|" + THEN + r")|"
    r"(?:write|describe|show|depict|tell)\s+(?:\w+\s+){0,4}?(?:back then|at the time|in those days|when (?:she|he|they) (?:was|were)))\b",
    re.IGNORECASE)
SCHOOL_STAFF_BEFORE = re.compile(
    r"\b(?:teachers?|staff|head ?teachers?|heads?|principals?|janitors?|caretakers?|governors?|cooks?|secretar(?:y|ies)|"
    r"nurses?|assistants?|parents?|mums?|moms?|dads?|colleagues?|coworkers?|work(?:s|ed|ing)?|job)\s+(?:at|in|of|from|for)\s+"
    r"(?:a|the|our|my|her|his|their|local|same)?\s*$", re.IGNORECASE)
SCHOOL_PLACE = re.compile(r"school", re.IGNORECASE)
GROWN_UP_PARTNER = re.compile(
    r"\b(?:teachers?|coach|tutor|uncle|step-?(?:dad|father|mum|mom|mother)|neighbou?r|boss|landlord|priest|pastor|"
    r"babysitter|older (?:man|men|woman|guy|lady)|grown (?:man|men|woman)|adults?|(?:his|her) (?:30|40|50|60)s|"
    r"(?:mr|mrs|ms)\.? [A-Z]\w*|(?:my|our) (?:students?|pupils?)|after class|detention|let'?s role-?play|role-?play with)\b",
    re.IGNORECASE)
NEGATABLE_TERMS = re.compile(r"explicit|sexual|sexy|steamy|spicy|sensual|x-rated|r-rated|nsfw|smut\w*|graphic|romantic|"
                             r"romance|intimate|naughty|erotic\w*|racy|raunchy", re.IGNORECASE)
NEGATED_BEFORE = re.compile(
    r"\b(?:no|not|nothing|never|without|non|zero|avoid(?:ing)?|don't|do not|isn't|is not|aren't|won't|free of)[^\w.:;!?]+"
    r"(?:(?:too|very|overly|remotely|really|anything|any|that|or|and|nor|scary|violent|gory|sexual|graphic|explicit|steamy|"
    r"spicy|romantic|sexy|write|say|include|add|put|show|describe|use)[^\w.:;!?]+){0,3}$",
    re.IGNORECASE)
SPARING_BEFORE = re.compile(r"\bspar(?:e|es|ed|ing)\s+$", re.IGNORECASE)
STILL_WANTED_AFTER = re.compile(
    r"^[^.!?\n]{0,50}?\b(?:left out|left behind|off the table|off-limits|off limits|restraint|restrictions?|limits?|"
    r"holding back|held back|hold back|filters?|filtered|censor\w*|barred|forbidden)\b", re.IGNORECASE)
TOO_MUCH_AFTER = re.compile(r"^\W*(?:for|to)\b", re.IGNORECASE)
TOO = re.compile(r"\btoo\b", re.IGNORECASE)
TOPIC_WORD = re.compile(r"(?:" + TOPIC_TERMS + r")", re.IGNORECASE)
NEGATABLE_WORDS = re.compile(r"\b(?:" + NEGATABLE_TERMS.pattern + r")\b", re.IGNORECASE)
NEGATION_LOOK_BACK_CHARS = 40
EARLIER_CONTEXTS_KEPT = 4
TEXTS_KEPT = 16
INNER_NEGATION = re.compile(r"(?:isn't|is not|aren't|are not|wasn't|was not|non)\W*$", re.IGNORECASE)
OUTER_NEGATION_BEFORE = re.compile(r"\b(?:no|not|nothing|never|without|don't|do not|won't|can't|cannot)\b[^.,;:!?]{0,40}$",
                                   re.IGNORECASE)
PAST_ROLE_CHARS = 30
NONFICTION = (r"(?:essays?|articles?|reports?|blogs?|blog posts?|posts?|summar(?:y|ies)|lesson plans?|lessons?|letters?|emails?|"
              r"reviews?|guides?|leaflets?|polic(?:y|ies)|speech(?:es)?|papers?|briefings?|explainers?|threads?|faqs?|plans?|"
              r"proposals?|user stor(?:y|ies)|news stor(?:y|ies)|press releases?)")
PAST_ROLE_BEFORE = re.compile(r"\b(?:(?:was|were|used to be|grew up as|once|formerly|former|as)\s+(?:a|an|the)?|"
                              r"(?:met|dated|together|sweethearts|friends|known each other|knew each other)\s+(?:\w+\s+){0,2}?"
                              r"(?:in|at|since|from)(?:\s+(?:a|the))?)\s*$", re.IGNORECASE)
SAFEGUARDING_PHRASES = re.compile(
    r"\b(?:child (?:sexual )?(?:abuse|exploitation)(?: material| imagery| images)?|csam|"
    r"sexual(?:ly)? (?:abuse[ds]?|abusing|assault(?:ed|s)?|exploit(?:ation|ed)|harass(?:ment|ed)|offen[cs]es?|predators?|"
    r"development|consent|grooming|abuse material)|sex (?:offen(?:der|ders|ces?|ses?)|crimes?|trafficking)|"
    r"child porn(?:ography)?|(?:porn|nudity|sexual content)\s+(?:filters?|blockers?|blocking|warnings?|ratings?|settings?))\b",
    re.IGNORECASE)
REQUEST_VERB = (r"(?:write|tell|give|make|create|generate|compose|draft|describe|narrate|continue|do|send|show|produce|"
                r"imagine|pretend|role-?play|draw|render|paint|depict|start|begin|let'?s|can you|could you|would you|will you|"
                r"i want|i'd like|i would like|i need|please)")
NOT_A_TALE = r"(?<!\buser )(?<!\bnews )(?<!\bsuccess )(?<!\bcover )"
CONTENT_NOUNS = (r"(?:" + NOT_A_TALE + r"stor(?:y|ies)|scenes?|role-?plays?|rp|fan-?fics?|fan fiction|erotica|smut|lemons?|chapters?|poems?|"
                 r"(?:film|movie|tv|stage)\s+scripts?|screenplays?|dialogues?|fantas(?:y|ies)|fiction|novels?|sexts?|images?|"
                 r"pictures?|pics?|photos?|drawings?|illustrations?|videos?|renders?|nudes?|messages?)")
STORY_NOUNS = (r"(?:" + NOT_A_TALE + r"stor(?:y|ies)|scenes?|role-?plays?|rp|fan-?fics?|fan fiction|erotica|smut|lemons?|chapters?|poems?|"
               r"screenplays?|fantas(?:y|ies)|fiction)")
CHARACTER_CARD = r"^[ \t]*(?:scenario|personality|persona|first message|greeting|char(?:acter)? card)\s*:"
ACTION_LINE = r"^[ \t]*\*[^*\n]*\b(?:i|me|my|you|your)\b[^*\n]*\*"
NOT_THE_OBJECT = r"(?!(?:for|of|about|on|from|regarding|in|with|to)\b)"
FREE_DIRECTIVES = (
    r"imagine|pretend|act as|play as|role-?play(?:ing)?|(?:you are|you're|ur)\s+(?:a|an|my|now)|"
    r"let'?s (?:play|pretend|roleplay|role-play|have|do|say)|talk dirty|dirty talk|sext(?:ing)? me|"
    r"(?:describe|detail|depict|portray|picture)\s+(?:a|an|her|him|his|them|their|my|your|what (?:she|he|they)|"
    r"how (?:she|he|they) (?:look|feel)|in detail)|"
    r"(?:write|tell|give)\s+(?:me\s+)?(?:something|anything|more)|write\s+(?:me\s+)?(?:an?\s+)?"
    r"(?!(?:\w+\s+){0,2}?" + NONFICTION + r"\b)(?:\w+\s+){0,2}"
    r"(?:about|involving|where|in which|starring)|what (?:did|does|do|will|would) \w+ do next|what happen(?:s|ed) next|"
    r"then what|continue|keep going|go on(?=\s*(?:$|[.!,;]))|carry on|next (?:part|chapter|scene)|"
    r"stay(?:ing)? in character|in character as|break(?:ing)? character|"
    r"(?:you are|you're|you will be|you'll be|you play|you're playing)\s+" + NAME + r"|"
    r"historia|relato|cuento|escena|histoire|geschichte|szene|storia|racconto|conto")
CONTENT_REQUEST = re.compile(
    r"\b" + REQUEST_VERB + r"\W+(?:" + NOT_THE_OBJECT + r"[\w'-]+\W+){0,5}?" + CONTENT_NOUNS + r"\b|"
    r"^\W*(?:" + NOT_THE_OBJECT + r"[\w'-]+\s+){0,4}?" + STORY_NOUNS + r"\b|\b(?:" + FREE_DIRECTIVES + r")\b|" + ACTION_LINE + r"|" + CHARACTER_CARD,
    re.IGNORECASE | re.MULTILINE)
WRITING_REQUEST = re.compile(r"\b" + REQUEST_VERB + r"\W+(?:[\w'-]+\W+){0,5}?" + NONFICTION + r"\b", re.IGNORECASE)
NONFICTION_WORDS = re.compile(r"\b(?:" + NONFICTION + r"|news|readers?)\b", re.IGNORECASE)
RETOLD_WORKS = re.compile(r"\b(?:films?|movies?|books?|novels?|songs?|lyrics?)\b", re.IGNORECASE)
TELLER_BEFORE_YOUNG = re.compile(
    r"\b(?:starring|featuring|told by|narrated by|in the voice of|from the (?:point of view|perspective|pov) of|in (?:the )?first "
    r"person as|quotes?\s+from|quoting|interview(?:s|ing)?(?:\s+with)?|(?:letters?|diary|diaries|testimony|statements?|words) "
    r"(?:of|from|by))\s+(?:(?:a|an|the|this|that)\s+)?$", re.IGNORECASE)
YOUNG_TELLS_AFTER = re.compile(
    r"[^.!?\n]{0,30}?\b(?:tells|told|describes|described|recounts|recounted|remembers|recalls|narrates|answers|explains|says|"
    r"talks about|speaks about|writes about|confides|confesses)\b", re.IGNORECASE)
TELLER_LOOK_BACK_CHARS = 40
NARRATION_MARKERS = (r"(?:moment[\s-]by[\s-]moment|minute[\s-]by[\s-]minute|step[\s-]by[\s-]step|blow[\s-]by[\s-]blow|"
                     r"scene[\s-]by[\s-]scene|in the style of|vivid(?:ly)?|nothing (?:is )?left out|leave nothing out|"
                     r"hold(?:ing)? nothing back)")
CONTENT_MARKERS = re.compile(
    r"\b(?:(?<!'s )tales?|retell(?:s|ing)?|in full(?=\s*(?:$|[,.;:!?)]|\s+(?:and|with|please|so|including|from|for|as|detail)\b))|"
    r"every (?:single |last )?detail|word for word|(?:first-?hand |personal |detailed )?accounts? of|" + NARRATION_MARKERS + r")\b",
    re.IGNORECASE)
NARRATED = re.compile(r"\b" + NARRATION_MARKERS + r"\b", re.IGNORECASE)
DESCRIBING = re.compile(r"\b(?:describe|detail|depict|portray|picture|walk me through)\s+(?:what|how|the way)\b", re.IGNORECASE)
ESCALATION = re.compile(
    r"\b(?:make|making|write|rewrite|turn)\s+(?:it|this|that|her|him|them|the (?:story|scene|chapter|next (?:part|chapter|scene)))\s+"
    r"(?:(?:a lot|much|even|way|a bit)\s+)?(?:more\s+)?" + r"(?:explicit|steamy|steamier|spicy|spicier|sexy|sexier|hot|hotter|naughty|"
    r"naughtier|x-rated|intimate|sensual|dirty|dirtier|graphic|nsfw|adult|r-rated)\b|"
    r"^\W*(?:now|ok|okay|and|then)?\W*(?:(?:a lot|much|even|way)\s+)?more\s+(?:explicit|steamy|spicy|sexy|graphic|intimate|"
    r"sensual|dirty|naughty)\b",
    re.IGNORECASE)
PURPOSE = re.compile(
    r"\b(?:abus\w*|groom\w*|safeguard\w*|protect\w*|report\w*|police|prevent\w*|exploit\w*|laws?|legal\w*|illegal|crimes?|"
    r"criminal|offen[cs]es?|offenders?|courts?|prosecut\w*|convict\w*|charged|jailed|sentenced|accused|arrested|trial|inquiry|"
    r"scandal|news|articles?|headlines?|consent|doctor|gp|nurse|patients?|p(?:a|ae|e)ediatric\w*|medical|health\w*|clinic|"
    r"hospital|vaccin\w*|symptoms?|rash\w*|swollen|infection|chlamydia|stis?|stds?|hpv|screening|pill|contracepti\w*|"
    r"condoms?|pregnan\w*|puberty|tanner|breastfeed\w*|therap\w*|counsel\w*|education|lessons?|leaflets?|guidance|polic(?:y|ies)|"
    r"code of conduct|governors|minutes|agenda|incidents?|disclosed|staff|filters?|block\w*|moderat\w*|parental controls?|"
    r"flag\w*|detect\w*|dataset|data|columns?|csv|sql|select|tables?|query|queries|plot|survey|demographic\w*|statistic\w*|"
    r"research|stud(?:y|ies)|risks?|dangers?|blog posts?|histor\w*|medieval|roman|bible|books?|essays?|lyrics?|songs?|films?|movies?|"
    r"summar\w*|explain\w*|meaning|what does|what is|normal|worried|nervous|furious|upset|scared|caught|found|walked in|"
    r"handle|deal with|help|advice|awkward|froze|told me|talk to|how (?:do|should|can) (?:i|we)|pressur\w*|bull(?:y|ied|ying)|"
    r"victims?|survivors?|helplines?|nspcc|childline|healthy|development|behaviou?r|suitable|unsuitable|appropriate|"
    r"inappropriate|all right for|alright for|ok for|okay for|fine for|rated|ratings?|certificates?|age limits?|old enough|"
    r"usually|typically|what age|when do|find(?:ing|s)? out|found out|forums?|"
    r"what (?:should|do|can) (?:i|we) do|should (?:i|we)|is (?:it|this|that) (?:ok|okay|normal|legal|illegal|safe|wrong)|"
    r"in the (?:play|book|novel|film|movie|show|series|bible|poem|song|news|article|law)|plays?|tell me about|"
    r"unit tests?|tests?|endpoints?|users?|api|functions?|code|refactor\w*|scripts?|bugs?|errors?|classes|methods?|"
    r"examples?|regex\w*|class|def|self|import|return|migrations?|age checks?|age verification|verif\w*)\b",
    re.IGNORECASE)
QUESTION_FORM = re.compile(
    r"(?:^|[.!?\n]\s*)(?:so\s+|and\s+|but\s+|ok\s+|okay\s+)?(?:what|who|how|why|when|where|which|is|are|was|were|can|could|"
    r"does|do|did|tell me about|summari[sz]e|explain|define|give me an? (?:example|summary|overview))\b",
    re.IGNORECASE)
FICTIONAL_AGE = 100
ADULTS_ONLY = re.compile(
    r"\b(?:adults?|grown[- ]?ups?|over (?:18|eighteen)|18\+|of age|consenting|both (?:18|19|[2-9]\d)|(?:all|everyone|both) "
    r"(?:are|is) (?:adults?|over))(?![\w+])",
    re.IGNORECASE)
YOUNG_ASSIGNMENT = re.compile(
    r"\b(?:make|making|made|makes)\s+(?:her|him|them|" + NAME + r")\s+" + AGE + AGE_END +
    r"|\b(?:she|he|they|" + NAME + r")(?:'s|\s+is|\s+are|\s+was|\s+becomes|\s+turns into)\s+(?:(?:a|an|now|just|only|still|actually|really|in fact)\s+)*"
    r"(?:\d{1,2}|" + SPELLED + r"|schoolgirl|schoolboy|child|kid|little (?:girl|boy)|minor|preteen|teen(?:ager)?|toddler)\b",
    re.IGNORECASE)
YOUNG_ASSIGNED_WORD = re.compile(r"\b(?:schoolgirl|schoolboy|child|kid|little (?:girl|boy)|minor|preteen|teen(?:ager)?|"
                                 r"toddler)\b", re.IGNORECASE)
YOUNG_WORD_AGE = 12
NAMED_YOUNG = re.compile(r"\b(" + NAME + r")\s+(?:is|was)\s+(?:(?:a|an|still|only|just)\s+)*(?:little\s+)?"
                         r"(?:schoolgirl|schoolboy|child|kid|minor|preteen|tween|toddler|little (?:girl|boy))\b", re.IGNORECASE)
SCHOOL_YEAR_AGE = re.compile(
    r"\b(?:is|was|are|were|'s|'re)\s+(?:only\s+|just\s+|still\s+)?(?:in|starting|entering)\s+(year|grade)\s+(\d{1,2}|"
    + "|".join(UNITS) + r"|ten|eleven|twelve)\b"
    r"(?!\s+(?:of|at)\s+(?:uni\w*|college|university|(?:my|the|her|his) degree|med school|law school))"
    r"(?!\s+of\s+(?!(?:(?:primary|secondary|middle|junior|high|elementary|grade)\s+)?school\b))", re.IGNORECASE)
SCHOOL_START_AGE = {"year": 4, "grade": 5, "class": 6, "form": 10, "S": 11, "P": 4, "Y": 4}
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7}
PUPILS = r"(?:pupils?|girls?|boys?|students?|kids?|child(?:ren)?|lass(?:es)?|lads?)"
SCHOOL_CLASS_AGE = re.compile(
    r"\b" + PEOPLE + r"\s+(?:(?:who|that)\s+)?(?:is\s+|was\s+)?(?:in|starting|entering)\s+(?:the\s+)?(?:"
    r"(?:(?P<number>[1-7])(?:st|nd|rd|th)|(?P<ordinal>" + "|".join(ORDINALS) + r"))\s+(?P<class>class|form)\b"
    r"(?!\s+(?:on|of|ticket|seat|carriage|cabin|lounge|degree|honours|stamps?|post|mail)\b)|"
    r"(?-i:(?P<stage>[SPY]))\s?(?P<stage_number>[1-9]|1[0-3])\b(?=\s*(?:$|[,.;:!?)]|\s+(?:at|in)\s+(?:\w+\s+)?school\b)))",
    re.IGNORECASE)
STAGE_PUPIL = re.compile(r"(?:(?<![\w-])(?-i:(?P<pupil_stage>[SPY]))\s?|\b(?P<primary>primary|secondary)\s+)(?P<pupil_number>[1-9]|1[0-3])\s+"
                         + PUPILS + r"\b", re.IGNORECASE)
SPACED_LETTERS = re.compile(r"\b[A-Za-z](?:(?: |\. ?|[-_/])[A-Za-z]){3,}(?![A-Za-z])\.?")
LETTER_GAPS = re.compile(r"[ ._/-]")
DOTTED_WORDS = re.compile(r"\b[A-Za-z0-9]+(?:\.[A-Za-z0-9]+){3,}\b")
SPLIT_NUMBER_WORDS = {word[:cut] + " " + word[cut:]: word for word in [*UNITS, *TEENS] if len(word) >= 5
                      for cut in range(2, len(word) - 1)}
SPLIT_NUMBER = re.compile(r"\b(?=[efnst])(?:" + "|".join(SPLIT_NUMBER_WORDS) + r")\b", re.IGNORECASE)
POSSESSIVE_BEFORE = re.compile(r"\b(?:my|our|your)\s+$", re.IGNORECASE)
WIDE_YOUNG = re.compile(
    r"\b(?:petite|tiny|flat-?chested|innocent|virgin|first period|braces|pigtails|school ?uniform|babysit\w*|students?|pupils?|"
    r"class(?:mates?|rooms?)|teachers?|coach|camp counsel\w*|youth (?:group|pastor)|barely legal|just turned|birthday|"
    r"grade|year \d+|little|young|small (?:girl|boy|body)|step-?(?:daughter|son|sister|brother)|daughters?|sons?|nieces?|"
    r"nephews?|anime|cartoon|underdeveloped|growing up|school|nursery|playground|bedtime)\b",
    re.IGNORECASE)
ABSENT = re.compile(
    r"\s*(?:are|is|'re|'s|were|was)\s+(?:(?:fast|sound|finally|all)\s+)?(?:asleep|sleeping|at|out|away|in bed|with|staying|gone|"
    r"upstairs|downstairs|over at|round at|off|not here|not home|at school|in school)\b",
    re.IGNORECASE)
CLAUSE_BREAK = re.compile(r"[.,;:!?\n]")
FENCED_CODE = re.compile(r"^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?(?:^[ \t]*\1[`~]*[ \t]*$|\Z)", re.MULTILINE | re.DOTALL)
LETTER_FOR_ONE = re.compile(r"(?<![^\W\d_])[lI|](?=\d\b)")
ROMAN_AGES = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10, "XI": 11,
              "XII": 12, "XIII": 13, "XIV": 14, "XV": 15, "XVI": 16, "XVII": 17, "XVIII": 18, "XIX": 19}
YEARS_AFTER = r"(?=[\s-]*(?:years?|yrs?)[\s-]*old\b|[\s-]*(?:yo|y/o)\b)"
ROMAN_AGE = re.compile(r"\b(?-i:(X?(?:IX|IV|V?I{1,3}|V)|X))\b" + YEARS_AFTER, re.IGNORECASE)
DOZEN_AGE = re.compile(r"\ba dozen\b" + YEARS_AFTER, re.IGNORECASE)
DECADE_AGE = re.compile(r"\b(?:a|one)\s+decade(?=\s+old\b)", re.IGNORECASE)
HYPHEN_CHAIN = re.compile(r"\b\w++(?:-\w++){3,}")
LONG_SPACE = re.compile(r"\s{40,}")
JOINED_WORDS = re.compile(r"(?<=[A-Za-z])[_+]+(?=[A-Za-z0-9])|(?<=[0-9])(?:_+|(?<!18)\++)(?=[A-Za-z])|(?<=[a-z])(?=[A-Z][a-z])")
LONG_TOKEN = re.compile(r"\S{" + str(LONG_TOKEN_CHARS) + r"}(?=\S)")
OWN_FAMILY_START = re.compile(r"(?:my|our|your)\b", re.IGNORECASE)
NOT_FAMILY = (r"(?!(?:book|story|stories|novel|game|script|role-?play|rp|fic|fan-?fic|world|comic|manga|series|show|film|movie|"
              r"campaign|character|oc|plot|chapter|scene|setting|universe|draft|manuscript)s?\b)")
OWN_FAMILY_BEFORE = re.compile(r"\b(?:my|our)\s+(?:" + NOT_FAMILY + r"\w+\s+){0,2}$", re.IGNORECASE)
SEXUAL_OBJECT_BEFORE = re.compile(r"(?:" + TOPIC.pattern + r")[^.!?\n]{0,25}\b(?:with|to|on|for|about|of|involving|between)\s+$",
                                  re.IGNORECASE)
SENTENCES = re.compile(r"(?<=[.!?])\s+|\n+")
YOUNG_ROLE = re.compile(
    r"\b(?:you are|you're|ur|be|play|playing|pretend to be|act as)\s+(?:my|our)\s+(?:(?:little|baby|younger|kid)\s+(?:step-?)?"
    r"(?:sister|brother|sis|bro|daughter|son|niece|nephew|cousin)|(?:step-?)?(?:daughter|niece|nephew)\b(?=[^.!?\n]{0,40}\b(?:little|young|small)\b))",
    re.IGNORECASE)
CONTENT_RULE = re.compile(
    r"\b(?:never|do not|don't|does not|doesn't|must not|mustn't|should not|shouldn't|cannot|can't|won't|will not|avoid\w*|"
    r"refus\w*|forbid\w*|prohibit\w*|not allowed|disallow\w*|no\s+(?:sexual|explicit|nsfw|adult|erotic))\b[^.!?\n]*"
    r"\b(?:involving|with|about|depicting|featuring|including|of|for|towards?|to)\s+(?:any\s+)?"
    r"(?:minors?|underage|under-age|children|kids|young people|teenagers|anyone under|anybody under|someone under|"
    r"people under|under (?:the age of )?(?:18|eighteen))\b",
    re.IGNORECASE)
ADULT_LIMIT = re.compile(r"\b(?:almost|nearly|not yet|not quite|under(?:\s+the\s+age\s+of)?|below|younger than|less than|"
                         r"over|above|at least)\s+(?:18|eighteen)\b", re.IGNORECASE)
SENTENCE_PIECES = re.compile(r"[^.!?\n]+[.!?]*")
SECOND_PERSON = re.compile(
    r"\b(?:your|yours|yourself)\b|(?<!\bcan )(?<!\bcould )(?<!\bwould )(?<!\bwill )(?<!\bthank )(?<!\bdo )\byou\b"
    r"(?!\s+(?:can|could|would|will|please|might|should|know|think|mind|write|tell|make|give|describe|continue)\b)",
    re.IGNORECASE)
ROLE_NAME = re.compile(r"\b(?:you are|you're|you play|you'll be|you will be|play as|playing as|i am|i'm|name\s*:)\s+(" + NAME + r")\b(?!'s)",
                       re.IGNORECASE)
CARD_AGE = re.compile(r"^[ \t]*age\s*[:=]\s*" + AGE, re.IGNORECASE | re.MULTILINE)
NAMED_HERE = re.compile(r"\b(?:with|and|for|about|of|to|between|as)\s+(" + NAME + r")\b")
THIRD_PERSON = re.compile(r"\b(?:she|he|they|her|him|them|his|their)\b", re.IGNORECASE)
ROLEPLAY_LINE = re.compile(r"^[ \t]*\*|\b(?:i|we)\s+(?:say|said|whisper\w*|tell|told|murmur\w*|ask)\b|\b(?:you|your)\b|"
                           r"^\W*(?:she|he|" + NAME + r")\s+\w+", re.IGNORECASE | re.MULTILINE)
INTIMATE_ACTION = re.compile(
    r"\b(?:lift\w*|pull\w*\s+(?:up|down|off|over)|unbutton\w*|unzip\w*|undo(?:es|ing)?|stroke\w*|caress\w*|kiss\w*|lick\w*|"
    r"touch\w*|rub\w*|fondl\w*|blankets?|covers|duvet|sheets|curtains?|lock\w*\s+the\s+door|(?:sit|lie|lay)\s+(?:down\s+)?"
    r"(?:next to|beside|on|with)\s+me|nobody\s+(?:has|needs|will|is going)\s+to\s+know|no one\s+(?:has|needs|will|is going)\s+to\s+"
    r"know|our secret|good (?:girl|boy)|as i showed you|keep\s+(?:very\s+)?still|skirts?|blouses?|nightie|pyjamas|pajamas|"
    r"neck|thighs?|lap|undress\w*|while (?:he|she|i) watch\w*)\b",
    re.IGNORECASE)
THE_YOUNG_ONE = re.compile(r"\bthe (?:girl|boy|child|kid|little one)\b", re.IGNORECASE)
PERSON_REFERENCE = re.compile(r"\b(?:she|he|her|him|they|them|the (?:girl|boy|kid|child|character|student|pupil|teen))\b",
                              re.IGNORECASE)
CODE_LINE = re.compile(
    r"^\s*(?:def |class |import |from \S+ import|return\b|elif\b|else:|try:|except\b|finally:|with .*:$|for .*:$|if .*:$|"
    r"while .*:$|#|//|/\*|\*|@\w|[\w.\[\]'\"]+\s*[-+*/]?=[^=]|.*[{}]\s*$|.*[()=\[\]<>'\"].*;\s*$|"
    r"[\w.$]+(?:\(\))?(?:\+\+|--)?;\s*$|.*[(\[,]\s*$|[)\]}]+[,;]?\s*$|```)")
CHECK_POINT = re.compile(r"[.!?\n。！？][\"'\u201d\u2019*_)\]]*\s*$")
WORD_BREAK = re.compile(r"[\s.!?,;:)\]、。！，？；]")
CONTINUATION = re.compile(
    r"^\s*(?:now|then|ok|okay|good|great|yes|and|next|more|again)\b|"
    r"\b(?:continue|keep going|go on(?=\s*(?:$|[.!,;]))|carry on|make (?:it|this) (?:more|longer)|next (?:part|chapter|scene)|"
    r"more detail)\b",
    re.IGNORECASE)


def plain_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text or ""))
    kept = "".join(char for char in decomposed if unicodedata.category(char) not in DROPPED_CATEGORIES)
    return unicodedata.normalize("NFKC", kept).translate(PLAIN_LETTERS)


def blanked(pattern: re.Pattern, text: str) -> str:
    return pattern.sub(lambda found: " " * len(found.group()), text)


def with_ages_in_digits(text: str) -> str:
    text = DOTTED_WORDS.sub(lambda found: found.group().replace(".", " "), text)
    text = SPACED_LETTERS.sub(lambda found: LETTER_GAPS.sub("", found.group()), text)
    text = SPLIT_NUMBER.sub(lambda found: SPLIT_NUMBER_WORDS[found.group().lower()], text)
    text = LETTER_FOR_ONE.sub("1", text)
    text = ROMAN_AGE.sub(lambda found: str(ROMAN_AGES[found.group(1)]), text)
    return DECADE_AGE.sub("10 years", DOZEN_AGE.sub("12", text))


def with_words_apart(text: str) -> str:
    unhyphenated = HYPHEN_CHAIN.sub(lambda found: found.group().replace("-", " "), with_ages_in_digits(text))
    return LONG_TOKEN.sub(lambda found: found.group() + " ", JOINED_WORDS.sub(" ", unhyphenated))


def one_space(run: re.Match) -> str:
    return "\n" if "\n" in run.group() else " "


def trailing_space_start(text: str) -> int:
    index = len(text)
    while index and text[index - 1].isspace():
        index -= 1
    return index


@functools.lru_cache(maxsize=TEXTS_KEPT)
def readable(text: str) -> str:
    return with_words_apart(plain_text(text))


def without_code_blocks(text: str) -> str:
    return FENCED_CODE.sub(lambda found: " " * len(found.group()) if looks_like_code(found.group()) else found.group(), text)


def age_value(spelled: str) -> int:
    number = spelled.replace(",", ".")
    if re.fullmatch(r"\d+(?:\.\d+)?", number):
        return int(float(number))
    words = re.split(r"[\s-]+", spelled.lower())
    return sum(UNITS.get(word) or TEENS.get(word) or TENS.get(word, 0) for word in words)


def stated_age(found: re.Match) -> int:
    age = age_value(found.group(found.lastindex))
    return age - 1 if ALMOST.search(found.group()) else age


def found_ages(text: str) -> list[tuple[int, int, int]]:
    return list(ages_found_in(text))


@functools.lru_cache(maxsize=TEXTS_KEPT)
def ages_found_in(text: str) -> tuple[tuple[int, int, int], ...]:
    return tuple(every_age_in(text))


def every_age_in(text: str) -> list[tuple[int, int, int]]:
    if not AGE_HINT.search(text):
        return []
    ages = [(found.start(), found.end(), stated_age(found)) for pattern in AGE_PATTERNS for found in pattern.finditer(text)]
    ages += [(found.start(), found.end(), int(found.group(1))) for found in BIRTHDAY.finditer(text)]
    this_year = datetime.date.today().year
    ages += [(found.start(), found.end(), this_year - birth_year(found.group(1), this_year)) for found in BORN_IN.finditer(text)]
    ages += [(found.start(), found.end(), ADULT_AGE - 1) for found in UNDER_ADULT.finditer(text)]
    ages += [(found.start(), found.end(), summed_age(found)) for found in SUMMED_AGE.finditer(text)]
    if YEARS_SHORT_HINT.search(text):
        ages += [(found.start(), found.end(), age_value(found.group(2)) - age_value(found.group(1)))
                 for found in YEARS_SHORT.finditer(text)]
    ages += half_of_the_speakers_age(text)
    if not COLLEGE_YEARS.search(text):
        ages += [(found.start(), found.end(), age_value(found.group(2)) + SCHOOL_START_AGE[found.group(1).lower()])
                 for found in SCHOOL_YEAR_AGE.finditer(text)]
        ages += [(found.start(), found.end(), school_class_age(found)) for found in SCHOOL_CLASS_AGE.finditer(text)]
        ages += [(found.start(), found.end(), stage_pupil_age(found)) for found in STAGE_PUPIL.finditer(text)]
    return ages


def summed_age(found: re.Match) -> int:
    return age_value(found.group(1)) + age_value(found.group(2))


def half_of_the_speakers_age(text: str) -> list[tuple[int, int, int]]:
    ages = []
    for found in HALF_MY_AGE.finditer(text):
        owner = OWNER_AGES[found.group(1).lower()].search(text)
        if owner:
            ages.append((found.start(), found.end(), age_value(owner.group(1)) // 2))
    return ages


def birth_year(written: str, this_year: int) -> int:
    if not written.startswith("'"):
        return int(written)
    this_century = this_year - this_year % CENTURY
    year = this_century + int(written[1:])
    return year if year <= this_year else year - CENTURY


def school_class_age(found: re.Match) -> int:
    if found.group("class"):
        number = int(found.group("number")) if found.group("number") else ORDINALS[found.group("ordinal").lower()]
        return number + SCHOOL_START_AGE[found.group("class").lower()]
    return int(found.group("stage_number")) + SCHOOL_START_AGE[found.group("stage")]


def stage_pupil_age(found: re.Match) -> int:
    stage = found.group("pupil_stage") or ("S" if found.group("primary").lower() == "secondary" else "P")
    return int(found.group("pupil_number")) + SCHOOL_START_AGE[stage]


def names_under_age(text: str) -> set[str]:
    latest = {}
    for pattern in NAMED_AGE_PATTERNS:
        for found in pattern.finditer(text):
            latest[found.group(1)] = max(latest.get(found.group(1), (-1, 0)), (found.start(), age_value(found.group(2))))
    for found in NAMED_YOUNG.finditer(text):
        latest[found.group(1)] = max(latest.get(found.group(1), (-1, 0)), (found.start(), YOUNG_WORD_AGE))
    return {name for name, (_, age) in latest.items() if age < ADULT_AGE}


def is_own_family(text: str, start: int) -> bool:
    return bool(OWN_FAMILY_BEFORE.search(text[max(0, start - OWN_FAMILY_CHARS):start]))


def instruction_part(text: str) -> str:
    if len(text) <= 2 * INSTRUCTION_CHARS:
        return text
    return text[:INSTRUCTION_CHARS] + "\n" + text[-INSTRUCTION_CHARS:]


def looks_like_code(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < CODE_LINES_NEEDED:
        return False
    return sum(1 for line in lines if CODE_LINE.match(line)) >= CODE_LINE_SHARE * len(lines)


def asks_for_content(text: str) -> bool:
    return not looks_like_code(text) and bool(CONTENT_REQUEST.search(instruction_part(text)))


def is_continuation(text: str) -> bool:
    return bool(CONTINUATION.search(text))


def says_everyone_is_adult(text: str) -> bool:
    return bool(ADULTS_ONLY.search(text))


def family_takes_part(text: str, found: re.Match) -> bool:
    before = text[max(0, found.start() - FAMILY_LOOK_BACK_CHARS):found.start()]
    as_object = re.search(r"(?:" + TOPIC.pattern + r")[^.!?\n]{0,25}\b(?:with|to|on|for|about|of|involving|between)\s+$",
                          before, re.IGNORECASE)
    if as_object:
        return True
    after = CLAUSE_BREAK.split(text[found.end():found.end() + FAMILY_LOOK_AHEAD_CHARS], 1)[0]
    return not ABSENT.match(after) and bool(TOPIC.search(after) or CREATIVE_ONLY.search(after))


def states_the_teens_age(text: str, teen: tuple[int, int], adult: tuple[int, int]) -> bool:
    if max(teen[0], adult[0]) - min(teen[1], adult[1]) > TEEN_ADULT_CHARS:
        return False
    between = text[min(teen[1], adult[1]):max(teen[0], adult[0])]
    return not OTHER_PERSON.search(between + " " + text[adult[0]:adult[1]])


def adult_spans_of(ages: list[tuple[int, int, int]]) -> list[tuple[int, int]]:
    return [(start, end) for start, end, age in ages if ADULT_AGE <= age < FICTIONAL_AGE]


def young_spans(text: str, creative: bool) -> list[tuple[int, int]]:
    return list(young_spans_in(text))


@functools.lru_cache(maxsize=TEXTS_KEPT)
def young_spans_in(text: str) -> tuple[tuple[int, int], ...]:
    return tuple(every_young_span_in(text))


def every_young_span_in(text: str) -> list[tuple[int, int]]:
    ages = found_ages(text)
    spans = [(start, end) for start, end, age in ages if age < ADULT_AGE]
    people = blanked(NOT_A_PERSON, text)
    family = list(FAMILY.finditer(people))
    spans += [(found.start(), found.end()) for found in family if family_takes_part(people, found)]
    for found in family:
        people = people[:found.start()] + " " * (found.end() - found.start()) + people[found.end():]
    at_college = bool(COLLEGE_YEARS.search(text))
    grown_up = any(age >= ADULT_AGE for _, _, age in ages)
    spans += [(found.start(), found.end()) for found in YOUNG_WORDS.finditer(people)
              if not (at_college and SCHOOL_YEAR_NAMES.search(found.group()))
              and not (grown_up and not SET_BACK_THEN.search(people)
                       and PAST_ROLE_BEFORE.search(people[max(0, found.start() - PAST_ROLE_CHARS):found.start()]))
              and not (SCHOOL_PLACE.search(found.group())
                       and SCHOOL_STAFF_BEFORE.search(people[max(0, found.start() - PAST_ROLE_CHARS):found.start()]))]
    spans += [(found.start(), found.end()) for found in NOUN_MINOR.finditer(people)]
    spans += [found.span() for found in YOUNG_ROLE.finditer(text)]
    spans += [(found.start(), found.end()) for found in TEEN_WORDS.finditer(people)
              if not any(states_the_teens_age(text, found.span(), adult) for adult in adult_spans_of(ages))]
    return spans


def sexual_spans(text: str, creative: bool, graphic_only: bool = False) -> list[tuple[int, int]]:
    return list(sexual_spans_in(text, creative, graphic_only))


@functools.lru_cache(maxsize=TEXTS_KEPT)
def sexual_spans_in(text: str, creative: bool, graphic_only: bool) -> tuple[tuple[int, int], ...]:
    looked_at = blanked(ALWAYS_SAFE_PHRASES, text)
    if not creative:
        looked_at = blanked(SAFEGUARDING_PHRASES, looked_at)
    return tuple((found.start(), found.end()) for found in (ACT if graphic_only else TOPIC).finditer(looked_at))


def is_negated(text: str, start: int, end: int) -> bool:
    window_start = max(0, start - NEGATION_LOOK_BACK_CHARS)
    negation = NEGATED_BEFORE.search(text[window_start:start])
    if not negation:
        return False
    negation_start = window_start + negation.start()
    if is_an_intensifier(text, negation_start, negation.group(), end):
        return False
    return not is_doubly_negated(text, negation_start, start)


def is_an_intensifier(text: str, negation_start: int, negation: str, end: int) -> bool:
    if SPARING_BEFORE.search(text[max(0, negation_start - NEGATION_LOOK_BACK_CHARS):negation_start]):
        return True
    after = text[end:end + NEGATION_LOOK_BACK_CHARS + 10]
    if STILL_WANTED_AFTER.search(after):
        return True
    return bool(TOO.search(negation)) and bool(TOO_MUCH_AFTER.search(after))


def without_negated_terms(text: str) -> str:
    return NEGATABLE_WORDS.sub(lambda found: " " * len(found.group()) if is_negated(text, found.start(), found.end())
                               else found.group(), text)


def is_doubly_negated(text: str, negation_start: int, term_start: int) -> bool:
    if not INNER_NEGATION.match(text[negation_start:term_start]):
        return False
    return bool(OUTER_NEGATION_BEFORE.search(text[max(0, negation_start - NEGATION_LOOK_BACK_CHARS):negation_start]))


def is_a_negated_term(text: str, found: re.Match) -> bool:
    term = found.group()
    if not NEGATABLE_TERMS.fullmatch(term) or TOPIC_WORD.fullmatch(term):
        return False
    return is_negated(text, found.start(), found.end())


def suggestive_spans(text: str) -> list[tuple[int, int]]:
    return [found.span() for found in CREATIVE_ONLY.finditer(blanked(ALWAYS_SAFE_PHRASES, text))
            if not is_a_negated_term(text, found)]


def name_spans(text: str, names: set[str]) -> list[tuple[int, int]]:
    if not names:
        return []
    exact = sorted(name for name in names if not name.islower())
    lower = sorted(name for name in names if name.islower())
    parts = [r"(?-i:" + "|".join(re.escape(name) for name in exact) + r")"] if exact else []
    parts += [re.escape(name) for name in lower]
    pattern = re.compile(r"\b(?:" + "|".join(parts) + r")\b", re.IGNORECASE)
    return [(found.start(), found.end()) for found in pattern.finditer(text)]


def close_together(first: list[tuple[int, int]], second: list[tuple[int, int]], distance: int = NEARBY_CHARS) -> bool:
    ordered = sorted(second)
    starts = [start for start, _ in ordered]
    for start, end in first:
        low = bisect.bisect_left(starts, start - distance - LONGEST_MATCH_CHARS)
        high = bisect.bisect_right(starts, end + distance)
        if any(max(start, other_start) - min(end, other_end) <= distance for other_start, other_end in ordered[low:high]):
            return True
    return False


def assigns_someone_young(text: str) -> bool:
    if any(pattern.search(text) for pattern in AGE_ASSIGNMENT_PATTERNS):
        return True
    for found in YOUNG_ASSIGNMENT.finditer(text):
        if POSSESSIVE_BEFORE.search(text[max(0, found.start() - FAMILY_LOOK_BACK_CHARS):found.start()]):
            continue
        if YOUNG_ASSIGNED_WORD.search(found.group()):
            return True
        if any(age < ADULT_AGE for _, _, age in found_ages(text[found.start():found.end() + LONGEST_MATCH_CHARS])):
            return True
    return False


def characters_under_age(text: str) -> bool:
    return any(age < ADULT_AGE and not is_own_family(text, start) for start, _, age in found_ages(text))


def is_blocked_from_exemption(text: str) -> bool:
    suggestive = [text[start:end].lower() for start, end in suggestive_spans(text)]
    return any(word != "adult content" for word in suggestive) or asks_for_content(text)


def own_family_subject(text: str, span: tuple[int, int]) -> bool:
    if not (OWN_FAMILY_START.match(text, span[0]) or is_own_family(text, span[0])):
        return False
    before = text[max(0, span[0] - FAMILY_LOOK_BACK_CHARS):span[0]]
    return not SEXUAL_OBJECT_BEFORE.search(before)


@functools.lru_cache(maxsize=TEXTS_KEPT)
def is_informational(text: str) -> bool:
    return bool(PURPOSE.search(text)) and not is_blocked_from_exemption(text)


def asks_for_information(text: str) -> bool:
    return bool(QUESTION_FORM.search(text) or PURPOSE.search(text)) and not is_blocked_from_exemption(text)


def young_signal(plain: str, context: "Context") -> bool:
    return bool(young_spans(plain, True) or name_spans(plain, context.names))


def is_a_content_rule(sentence: str) -> bool:
    if not CONTENT_RULE.search(sentence):
        return False
    return not any(age < ADULT_AGE for _, _, age in found_ages(ADULT_LIMIT.sub(" ", sentence)))


def without_content_rules(text: str) -> str:
    return SENTENCE_PIECES.sub(lambda found: " " * len(found.group()) if is_a_content_rule(found.group()) else found.group(), text)


class Context:
    def __init__(self, context: str = ""):
        whole = without_content_rules(without_code_blocks(readable((context or "")[-CONTEXT_CHARS:])))
        recent = whole[-RECENT_CHARS:]
        self.names = names_under_age(whole)
        self.young_characters = characters_under_age(whole)
        recent_young = bool(young_spans(recent, True) or name_spans(recent, self.names))
        self.any_young = bool(self.names or self.young_characters or recent_young)
        self.recent_sexual = bool(sexual_spans(recent, True) or sexual_spans(recent, False, graphic_only=True))
        self.sexual_young = self.recent_sexual and recent_young
        self.creative_young = recent_young and asks_for_content(recent)
        self.has_young_character = bool(self.names or self.young_characters)
        self.young_role = plays_a_young_role(whole, self.names)


def plays_a_young_role(text: str, names: set[str]) -> bool:
    if YOUNG_ROLE.search(text) or any(age_value(found.group(1)) < ADULT_AGE for found in CARD_AGE.finditer(text)):
        return True
    return bool(names) and any(found.group(1) in names for found in ROLE_NAME.finditer(text))


@functools.lru_cache(maxsize=EARLIER_CONTEXTS_KEPT)
def earlier_context(context: str) -> Context:
    return Context(context)


def adult_spans(text: str) -> list[tuple[int, int]]:
    spans = [(found.start(), found.end()) for found in ADULTS_ONLY.finditer(text)]
    return spans + adult_spans_of(found_ages(text))


def young_counts(sexual: tuple[int, int], young: list[tuple[int, int]], adults: list[int], distance: int) -> bool:
    for young_start, young_end in young:
        if max(sexual[0], young_start) - min(sexual[1], young_end) > distance:
            continue
        low, high = (young_end, sexual[0]) if young_end <= sexual[0] else (sexual[1], young_start)
        if not any(low <= adult < high for adult in adults):
            return True
    return False


def creative_hit(sexual: list[tuple[int, int]], young: list[tuple[int, int]], adults: list[int], distance: int) -> bool:
    return any(young_counts(span, young, adults, distance) for span in sexual)


def refers_to_someone(text: str, context: Context) -> bool:
    return bool(PERSON_REFERENCE.search(text) or (context.has_young_character and SECOND_PERSON.search(text)))


def carried_young(text: str, context: Context, sexual: list[tuple[int, int]], adults: list[int]) -> bool:
    if not (context.young_characters or context.sexual_young) or not refers_to_someone(text, context):
        return False
    return any(all(adult > start for adult in adults) for start, _ in sexual)


def content_request_crosses(text: str, context: Context, continued: bool) -> bool:
    sexual = sexual_spans(text, True)
    suggestive = suggestive_spans(text)
    young = young_spans(text, True) + name_spans(text, context.names)
    adults = [start for start, _ in adult_spans(text)]
    if creative_hit(sexual, young, adults, CREATIVE_NEARBY_CHARS) or creative_hit(suggestive, young, adults, NEARBY_CHARS):
        return True
    if continued and context.sexual_young and (sexual or suggestive) and not adults:
        return True
    if ESCALATION.search(text) and (context.sexual_young or context.creative_young) and not adults:
        return True
    return bool(sexual) and carried_young(text, context, sexual, adults)


def same_sentence_hit(text: str, names: set[str], terms: re.Pattern | None = None) -> bool:
    for sentence in SENTENCES.split(text):
        looked_at = blanked(ALWAYS_SAFE_PHRASES, sentence)
        sexual = [found.span() for found in terms.finditer(looked_at)] if terms else sexual_spans(sentence, False)
        if sexual:
            young = [span for span in young_spans(sentence, False) + name_spans(sentence, names)
                     if not own_family_subject(sentence, span)]
            if close_together(sexual, young):
                return True
    return False


def plain_text_crosses(text: str, context: Context, informational: bool, within_a_sentence: bool = True) -> bool:
    young = young_spans(text, False) + name_spans(text, context.names)
    if not young:
        return False
    graphic = sexual_spans(text, False, graphic_only=True)
    if graphic and close_together(graphic, young, ACT_NEARBY_CHARS):
        return True
    if informational:
        return False
    if within_a_sentence:
        return same_sentence_hit(text, context.names)
    return close_together(sexual_spans(text, False), young)


def has_no_sexual_term_or_context(plain: str, context: str) -> bool:
    return not (context or "").strip() and not ANY_SEXUAL.search(plain)


def sexual_with_minors(text: str, context: str = "") -> bool:
    plain = without_code_blocks(readable(text))
    if not plain.strip() or looks_like_code(plain) or has_no_sexual_term_or_context(plain, context):
        return False
    earlier = earlier_context(context)
    continued = (earlier.recent_sexual or earlier.creative_young) and is_continuation(plain)
    disguised = is_a_disguised_tale(plain, earlier)
    if (asks_for_content(plain) or continued or disguised) and content_request_crosses(plain, earlier, continued):
        return True
    if plain_text_crosses(plain, earlier, is_informational(plain) and not disguised):
        return True
    return earlier.recent_sexual and assigns_someone_young(plain)


@functools.lru_cache(maxsize=TEXTS_KEPT)
def is_a_disguised_tale(plain: str, context: Context) -> bool:
    retold = bool(CONTENT_MARKERS.search(plain))
    if not (retold or (WRITING_REQUEST.search(plain) and is_told_by_the_young_one(plain, context.names))):
        return False
    without_the_wrapper = blanked(NONFICTION_WORDS, plain)
    if retold:
        without_the_wrapper = blanked(RETOLD_WORKS, without_the_wrapper)
    if not NARRATED.search(plain) and PURPOSE.search(without_the_wrapper) and not is_blocked_from_exemption(plain):
        return False
    return same_sentence_hit(plain, context.names)


def is_told_by_the_young_one(plain: str, names: set[str]) -> bool:
    for start, end in young_spans(plain, True) + name_spans(plain, names):
        if TELLER_BEFORE_YOUNG.search(plain[max(0, start - TELLER_LOOK_BACK_CHARS):start]):
            return True
        if YOUNG_TELLS_AFTER.match(plain, end):
            return True
    return False


def is_a_writing_request_with_both_signals(plain: str, context: Context) -> bool:
    if not (WRITING_REQUEST.search(plain) or CONTENT_MARKERS.search(plain) or CONTENT_REQUEST.search(plain)):
        return False
    return same_sentence_hit(plain, context.names)


@functools.lru_cache(maxsize=TEXTS_KEPT)
def asks_for_more(plain: str, context: Context) -> bool:
    if is_a_disguised_tale(plain, context):
        return True
    if CONTENT_REQUEST.search(plain) or ESCALATION.search(plain) or (DESCRIBING.search(plain) and not is_informational(plain)):
        return True
    return (context.recent_sexual or context.creative_young) and is_continuation(plain)


def strongly_sexual(plain: str) -> bool:
    return bool(sexual_spans(plain, False) or sexual_spans(plain, False, graphic_only=True) or suggestive_spans(plain))


def without_own_replies(text: str) -> str:
    return text.replace(MINORS_REPLY, " ").replace(CANT_CHECK_REPLY, " ")


def certain_without_a_closer_look(text: str, context: str = "") -> bool:
    said = without_own_replies(text)
    return certainly_young_and_sexual(said, context) and needs_a_closer_look(said, context)


def is_plainly_informational(plain: str) -> bool:
    return is_informational(plain) and not sexual_spans(plain, False, graphic_only=True)


def needs_a_closer_look(text: str, context: str = "") -> bool:
    plain = readable(text)
    earlier = earlier_context(context)
    young_here = young_signal(plain, earlier) or bool(WIDE_YOUNG.search(plain) and ANY_SEXUAL.search(plain))
    if is_an_intimate_line_to_a_young_role(plain, earlier):
        return True
    if asks_for_more(plain, earlier):
        return young_here or earlier.any_young
    if is_plainly_informational(plain) and not is_a_writing_request_with_both_signals(plain, earlier):
        return False
    carried = earlier.has_young_character and refers_to_someone(plain, earlier)
    return strongly_sexual(plain) and (young_here or carried)


def unnegated_terms(plain: str, pattern: re.Pattern) -> list[re.Match]:
    return list(unnegated_terms_in(plain, pattern))


@functools.lru_cache(maxsize=TEXTS_KEPT)
def unnegated_terms_in(plain: str, pattern: re.Pattern) -> tuple[re.Match, ...]:
    looked_at = blanked(INNOCENT_USES, blanked(ALWAYS_SAFE_PHRASES, plain))
    return tuple(found for found in pattern.finditer(looked_at) if not is_a_negated_term(plain, found))


def is_family_affection(plain: str) -> bool:
    if not OWN_FAMILY_CARE.search(plain):
        return False
    return all(INNOCENT_TERMS.fullmatch(found.group()) for found in unnegated_terms(plain, ANY_SEXUAL))


def grooming_acts(plain: str) -> list[re.Match]:
    acts = list(GROOMING_ACTS.finditer(plain))
    if not acts or not is_family_affection(plain):
        return acts
    return [found for found in acts if not FAMILY_CONTACT.fullmatch(found.group())]


def has_a_strong_signal(plain: str) -> bool:
    return bool(unnegated_terms(plain, STRONG_SEXUAL) or grooming_acts(plain))


def has_a_grown_up(plain: str) -> bool:
    return bool(GROWN_UP_PARTNER.search(plain)) or any(ADULT_AGE <= age < FICTIONAL_AGE for _, _, age in found_ages(plain))


def is_a_private_setting(plain: str) -> bool:
    return bool(PRIVATE_SETTING.search(plain) or (PARTNER_SETTING.search(plain) and has_a_grown_up(plain)))


def has_a_telling_term(plain: str) -> bool:
    if has_a_strong_signal(plain):
        return True
    if not unnegated_terms(plain, ANY_SEXUAL) or is_family_affection(plain):
        return False
    return is_a_private_setting(plain) or is_a_romance_with_a_grown_up(plain)


def is_a_romance_with_a_grown_up(plain: str) -> bool:
    romance = [found for found in ROMANCE_TERMS.finditer(plain) if not is_negated(plain, found.start(), found.end())]
    if not romance or PARENT_ROMANCE.search(plain) or PEER_ROMANCE.search(plain):
        return False
    return bool(GROWN_UP_PARTNER.search(plain))


def has_any_signal(plain: str) -> bool:
    return bool(unnegated_terms(plain, ANY_SEXUAL) or grooming_acts(plain))


def continues_a_young_sexual_scene(plain: str, earlier: Context) -> bool:
    return is_continuation(plain) and earlier.sexual_young


def acts_on_the_young_character(plain: str, earlier: Context) -> bool:
    if not earlier.has_young_character:
        return False
    if earlier.young_role and SECOND_PERSON.search(plain) and has_any_signal(plain):
        return True
    return bool(THE_YOUNG_ONE.search(plain)) and bool(grooming_acts(plain) or uninnocent_terms(plain))


def uninnocent_terms(plain: str) -> list[re.Match]:
    return [found for found in unnegated_terms(plain, ANY_SEXUAL) if not INNOCENT_TERMS.fullmatch(found.group())]


def is_an_intimate_line_to_a_young_role(plain: str, earlier: Context) -> bool:
    if not INTIMATE_ACTION.search(plain) or not ROLEPLAY_LINE.search(plain):
        return False
    return earlier.young_role or plays_a_young_role(plain, names_under_age(plain))


def refers_to_the_earlier_young(plain: str, earlier: Context) -> bool:
    if not earlier.young_characters:
        return False
    if THIRD_PERSON.search(plain) or ESCALATION.search(plain) or is_continuation(plain):
        return True
    return any(found.group(1) not in names_given_an_age(plain) for found in NAMED_HERE.finditer(plain))


def names_given_an_age(plain: str) -> set[str]:
    return {found.group(1) for pattern in NAMED_AGE_PATTERNS for found in pattern.finditer(plain)}


def certainly_young_and_sexual(text: str, context: str = "") -> bool:
    plain = readable(text)
    if not has_any_signal(plain) and not is_continuation(plain):
        return False
    earlier = earlier_context(context)
    if acts_on_the_young_character(plain, earlier):
        return True
    if not has_a_telling_term(plain):
        return continues_a_young_sexual_scene(plain, earlier)
    if sexual_spans(plain, False, graphic_only=True) and young_signal(plain, earlier):
        return True
    if not asks_for_more(plain, earlier):
        return False
    return young_signal(plain, earlier) or earlier.sexual_young or refers_to_the_earlier_young(plain, earlier)


def refused_without_a_judge(text: str, context: str = "") -> bool:
    said = without_own_replies(text)
    return certain_without_a_closer_look(said, context) and has_a_strong_signal(readable(said))


def unsafe_without_a_judge(text: str, context: str = "") -> bool:
    if certainly_young_and_sexual(text, context):
        return True
    return is_an_intimate_line_to_a_young_role(readable(without_own_replies(text)), earlier_context(context))


def needs_the_judge_to_clear(text: str, context: str = "") -> bool:
    said = without_own_replies(text)
    if certain_without_a_closer_look(said, context):
        return not has_a_strong_signal(readable(said))
    return is_an_intimate_line_to_a_young_role(readable(said), earlier_context(context))


class ReplyGuard:
    def __init__(self, context: str = "", request: str | None = None):
        asked = without_code_blocks(readable(request if request is not None else (context or "")[-RECENT_CHARS:]))
        self.context = Context(context)
        self.creative = (asks_for_content(asked) or (self.context.recent_sexual and is_continuation(asked))
                         or is_a_disguised_tale(asked, self.context))
        self.carried = self.creative and (self.context.sexual_young or self.context.has_young_character
                                          or young_signal(asked, self.context))
        self.informational = asks_for_information(asked)
        self.skips_code = not (self.creative or self.context.sexual_young or self.context.creative_young)
        self.kept = ""
        self.dropped = 0
        self.unchecked = 0
        self.last_young = None
        self.last_sexual = None
        self.last_plain_young = None
        self.last_plain_graphic = None
        self.last_adult = None
        self.first_adult = None
        self.last_any_sexual = None
        self.purposeful = is_informational(asked)
        self.asked_about_a_young_name = bool(name_spans(asked, self.context.names)) and not self.purposeful
        self.tripped = False

    def add(self, piece: str) -> bool:
        if self.tripped or not piece:
            return self.tripped
        plain = plain_text(piece)
        self.keep(plain)
        if self.unchecked < CHECK_EVERY_CHARS and not CHECK_POINT.search(self.kept[-len(plain) - 1:]):
            return False
        return self.check(self.last_word_break())

    def keep(self, plain: str) -> None:
        checked_end = len(self.kept) - self.unchecked
        space_start = trailing_space_start(self.kept)
        joined = self.kept[space_start:] + plain
        squeezed = LONG_SPACE.sub(one_space, joined)
        if squeezed == joined:
            self.kept += plain
            self.unchecked += len(plain)
            return
        self.kept = self.kept[:space_start] + squeezed
        self.unchecked = len(self.kept) - min(checked_end, space_start)

    def finish(self) -> bool:
        if not self.tripped and self.unchecked:
            self.check(len(self.kept))
        return self.tripped

    def last_word_break(self) -> int:
        checked = len(self.kept) - self.unchecked
        lowest = max(checked, len(self.kept) - MAX_UNBROKEN_CHARS)
        for index in range(len(self.kept) - 1, lowest - 1, -1):
            if WORD_BREAK.match(self.kept, index):
                return index + 1
        return len(self.kept) if self.unchecked >= MAX_UNBROKEN_CHARS else checked

    def check(self, end: int) -> bool:
        if end <= len(self.kept) - self.unchecked:
            return self.tripped
        fresh_chars = end - (len(self.kept) - self.unchecked) + LONGEST_MATCH_CHARS
        window = without_negated_terms(with_words_apart(self.kept[:end]))
        if self.skips_code:
            window = without_code_blocks(window)
            if looks_like_code(window):
                window = ""
        offset = self.dropped
        self.unchecked = len(self.kept) - end
        trimmed = max(0, end - OVERLAP_CHARS)
        self.kept = self.kept[trimmed:]
        self.dropped += trimmed
        fresh = window[-fresh_chars:]
        self.remember_sexual_terms(fresh, offset + len(window) - len(fresh))
        if not self.might_matter(fresh):
            return self.tripped
        if self.has_nothing_sexual_to_pair(offset):
            self.remember_people(fresh, offset + len(window) - len(fresh))
            return self.tripped
        if self.creative:
            self.tripped = self.creative_check(window, offset, sexual_spans(window, True))
        else:
            self.context.names = self.context.names | names_under_age(window)
            self.tripped = (plain_text_crosses(window, self.context, self.informational, within_a_sentence=False)
                            or self.plain_check(window, offset))
        self.tripped = self.tripped or self.makes_someone_young(window)
        return self.tripped

    def plain_check(self, window: str, offset: int) -> bool:
        if self.purposeful:
            return False
        if self.asked_about_a_young_name and sexual_spans(window, False) and not adult_spans(window):
            return True
        young = [(offset + start, offset + end) for start, end in young_spans(window, False)]
        if self.last_plain_young is not None:
            young.append((self.last_plain_young, self.last_plain_young))
        adults = [offset + start for start, _ in adult_spans(window)] + ([self.last_adult] if self.last_adult is not None else [])
        graphic = [(offset + start, offset + end) for start, end in sexual_spans(window, False, graphic_only=True)]
        if self.last_plain_graphic is not None:
            graphic.append((self.last_plain_graphic, self.last_plain_graphic))
        self.last_plain_young = max((end for _, end in young), default=self.last_plain_young)
        self.last_plain_graphic = max((end for _, end in graphic), default=self.last_plain_graphic)
        self.last_adult = max(adults, default=None)
        return any(young_counts(span, young, adults, len(self.kept) + self.dropped) for span in graphic)

    def remember_sexual_terms(self, fresh: str, fresh_offset: int) -> None:
        found = ANY_SEXUAL.search(fresh) or TOPIC.search(fresh)
        if found:
            self.last_any_sexual = fresh_offset + found.start()

    def has_nothing_sexual_to_pair(self, window_offset: int) -> bool:
        if self.last_sexual is not None or self.last_plain_graphic is not None or self.context.recent_sexual:
            return False
        return self.last_any_sexual is None or self.last_any_sexual < window_offset

    def remember_people(self, fresh: str, fresh_offset: int) -> None:
        if self.creative:
            young = young_spans(fresh, True) + name_spans(fresh, self.context.names)
            self.last_young = max((fresh_offset + end for _, end in young), default=self.last_young)
        else:
            self.context.names = self.context.names | names_under_age(fresh)
            young = young_spans(fresh, False)
            self.last_plain_young = max((fresh_offset + end for _, end in young), default=self.last_plain_young)
        adults = [fresh_offset + start for start, _ in adult_spans(fresh)]
        self.last_adult = max([*adults, *([self.last_adult] if self.last_adult is not None else [])], default=None)
        if self.creative and adults:
            self.first_adult = min([*adults, *([self.first_adult] if self.first_adult is not None else [])])

    def might_matter(self, fresh: str) -> bool:
        if ANY_SEXUAL.search(fresh) or ADULTS_ONLY.search(fresh) or name_spans(fresh, self.context.names):
            return True
        return bool(young_spans(fresh, True))

    def makes_someone_young(self, window: str) -> bool:
        return self.context.recent_sexual and not self.informational and assigns_someone_young(window)

    def creative_check(self, window: str, offset: int, sexual: list[tuple[int, int]]) -> bool:
        young = [(offset + start, offset + end) for start, end in young_spans(window, True) + name_spans(window, self.context.names)]
        adults = [offset + start for start, _ in adult_spans(window)] + ([self.last_adult] if self.last_adult is not None else [])
        sexual = [(offset + start, offset + end) for start, end in sexual]
        if self.last_young is not None:
            young.append((self.last_young, self.last_young))
        if self.last_sexual is not None:
            sexual.append((self.last_sexual, self.last_sexual))
        first_adult = min([self.first_adult, *adults], key=lambda value: float("inf") if value is None else value)
        carried = self.carried and any(first_adult is None or first_adult > start for start, _ in sexual)
        hit = carried or any(young_counts(span, young, adults, len(self.kept) + self.dropped) for span in sexual)
        self.last_young = max((end for _, end in young), default=self.last_young)
        self.last_sexual = max((end for _, end in sexual), default=self.last_sexual)
        self.last_adult = max(adults, default=None)
        self.first_adult = first_adult
        return hit

    def __call__(self, piece: str) -> bool:
        return self.add(piece)


def reply_crosses_the_line(answer: str, context: str = "", request: str | None = None) -> bool:
    guard = ReplyGuard(context, request)
    guard.add(answer or "")
    return guard.finish()


def declined_reply(answer: str, context: str, request: str | None = None) -> str | None:
    return MINORS_REPLY if reply_crosses_the_line(answer, context, request) else None


def text_values(value) -> list[str]:
    texts, waiting = [], [value]
    while waiting:
        item = waiting.pop()
        if isinstance(item, str):
            texts.append(item)
        elif isinstance(item, dict):
            for key, inner in reversed(list(item.items())):
                waiting += [inner, str(key)]
        elif isinstance(item, list):
            waiting += reversed(item)
    return texts


def call_arguments(call) -> str:
    if not isinstance(call, dict):
        return ""
    function = call.get("function") if isinstance(call.get("function"), dict) else call
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (ValueError, RecursionError):
            return arguments
    return "\n".join(text_values(arguments))


def calls_cross_the_line(content: str, calls, context: str = "", request: str | None = None) -> bool:
    texts = [content or "", *(call_arguments(call) for call in calls or [])]
    return reply_crosses_the_line("\n".join(text for text in texts if text), context, request)


SYSTEM_ROLES = ("system", "developer")
GUARD_ROLES = (*SYSTEM_ROLES, "user", "assistant")


def system_messages(messages: list) -> list:
    return [message for message in messages if message.get("role") in SYSTEM_ROLES]


def earlier_text(texts) -> str:
    return "\n".join(texts)[-CONTEXT_CHARS:]


class GuardedText:
    def __init__(self, on_text, context: str, request: str | None = None):
        self.on_text = on_text
        self.guard = ReplyGuard(context, request)
        self.stopped = False

    def __call__(self, piece: str):
        self.stopped = self.guard.add(piece)
        if not self.stopped:
            self.on_text(piece)

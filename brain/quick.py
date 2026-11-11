"""Quick actions: everyday requests that code carries out directly, rather than trusting a small model to
fill in a tool call (it mangles the arguments, then says it worked anyway). Each parser returns the parts
of a request it recognises, or None to leave the request to the model. brain.quick_action does the work.
"""
import ast
import operator
import re

ASKING = r"^\W*(?:(?:hey |hi )?arty,?\s*)?(?:(?:please|can you|could you|would you|will you)\s+)*"
# "put the dentist in my calendar next Tuesday at 3pm", "add football to my diary for Saturday 10am"
CALENDAR_ADD = re.compile(ASKING + r"(?:put|add|book|pop|stick|schedule)\s+(?P<what>.+?)\s+(?:in|into|on|to)\s+(?:my|the)\s+"
                          r"(?:calendar|diary)\b[\s,]*(?:for\s+|on\s+|at\s+)?(?P<when>.*?)[\s.!?]*$", re.IGNORECASE)
# "add milk and eggs to my shopping list", "put call mum on the to-do list"
LIST_ADD = re.compile(ASKING + r"(?:add|put|stick|pop)\s+(?P<items>.+?)\s+(?:to|on|onto)\s+(?:my|the)\s+(?P<list>[\w -]{1,30}?)\s*list"
                      r"[\s.!?]*$", re.IGNORECASE)
# "what's 12 times 15", "15% of 80", "what is 7.5 divided by 3": plain arithmetic, worked out exactly.
SUM_QUESTION = re.compile(ASKING + r"(?P<asked>what(?:'s| is)\s+|calculate\s+|work out\s+)?(?P<sum>[\d.,\s()%+*/x×÷^-]*\d[\w\s.,()%+*/×÷^-]*?)"
                          r"[\s?=.!]*$", re.IGNORECASE)
SUM_WORDS = [(r"\bmultiplied by\b|\btimes\b|[x×]", "*"), (r"\bdivided by\b|÷", "/"), (r"\bplus\b", "+"), (r"\bminus\b", "-"),
             (r"\bsquared\b", "**2"), (r"\bcubed\b", "**3"), (r"\bto the power of\b|\^", "**"), (r",(?=\d{3}\b)", "")]
DATE_OR_RANGE = re.compile(r"\d{1,4}\s*[-/]\s*\d{1,2}(?:\s*[-/]\s*\d{1,4})?")
PERCENT_OF = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*of\s*(\d+(?:\.\d+)?)")
MAX_POWER = 100
# "what are the IPs of my servers", "what's the IP address of the minecraft server"
IP_QUESTION = re.compile(r"\b(ips?|ip address(es)?)\b", re.IGNORECASE)
ABOUT_THE_SERVERS = re.compile(r"\b(servers?|homelab|guests?|containers?|vms?|cts?|lxcs?|proxmox|nodes?|machines)\b", re.IGNORECASE)
NOT_THE_SERVERS = re.compile(r"\b(public|external|wan|website'?s?|site'?s?|domain|router'?s?|phone'?s?|what is an)\b", re.IGNORECASE)
# Things Arty has no way to do, answered plainly by code: a small model asked these tends to pretend, or
# reaches for an unrelated tool. (Calls and texts are phone.py's: the phone app can open them.)
CANT_DO = [
    (re.compile(ASKING + r"(?:turn|switch|put|set)\s+(?:on\s+|off\s+|up\s+|down\s+)?(?:the\s+|my\s+)?(?:[\w-]+\s+){0,2}"
                r"(?:lights?|lamps?|heating|thermostat|boiler|kettle|plugs?|fans?|air ?con)\b", re.IGNORECASE),
     "I can't do that: I'm not connected to any smart-home devices (lights, heating, plugs and so on), so nothing has changed."),
    (re.compile(ASKING + r"(?:book|reserve)\s+(?:me\s+|us\s+)?(?:a\s+)?(?:table|restaurant|hotel|room|flight|train|taxi|"
                r"appointment|haircut|ticket)", re.IGNORECASE),
     "I can't make bookings for you: I can't contact businesses or pay for anything. Once you've booked it, tell me "
     "something like \"put Nando's in my calendar on Friday at 7pm\" and I'll add it."),
    (re.compile(ASKING + r"(?!.*\b(?:recipes?|ideas?|suggestions?|options|recommendations?|inspiration|menus?)\b)"
                r"(?:order|buy|purchase|pay for|get)\s+(?:me\s+|us\s+)?.{0,40}\b(?:pizza|takeaway|food|dinner|lunch|"
                r"taxi|uber|groceries)\b|\b(?:send|transfer|pay)\s+(?:(?:£|\$|€)\s?\d|\d+(?:\.\d+)?\s*(?:pounds?|quid|dollars?|"
                r"euros?|gbp|usd|eur)\b)", re.IGNORECASE),
     "I can't order, buy or pay for things for you: I don't handle payments. I can look up options or prices if that helps."),
]
REFERS_BACK = re.compile(r"\b(?:those|these|them|that one|this one|which one|the (?:first|second|third|last) one|above|"
                         r"you (?:just )?(?:said|mentioned|suggested|listed)|earlier|of the(?:m| ones))\b", re.IGNORECASE)
PASTED_TEXT_CHARS = 300


def refers_back(text: str) -> bool:
    text = text or ""
    return bool(REFERS_BACK.search(text)) or "\n" in text.strip() or len(text) > PASTED_TEXT_CHARS


MORE_THAN_ONE_THING = re.compile(r"\b(and then|then also|also)\b|;", re.IGNORECASE)


def _evaluate(node):
    """Arithmetic only: numbers and + - * / ** with brackets (no names, calls or anything else)."""
    operators = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
                 ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in operators:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_POWER:
            raise ValueError("too big")
        return operators[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in operators:
        return operators[type(node.op)](_evaluate(node.operand))
    raise ValueError("not arithmetic")


def calculate(text: str) -> str | None:
    """"12 × 15 = 180" for a plain arithmetic question, or None for anything else."""
    match = SUM_QUESTION.search(text or "")
    if not match or (DATE_OR_RANGE.fullmatch(match["sum"].strip()) and not match["asked"]):
        return None
    expression = PERCENT_OF.sub(r"(\1/100*\2)", match["sum"].strip())
    for words, symbol in SUM_WORDS:
        expression = re.sub(words, symbol, expression, flags=re.IGNORECASE)
    if not re.fullmatch(r"[\d.\s()+*/-]+", expression) or not re.search(r"\d\s*(\*\*|[+*/-])\s*[\d(-]", expression):
        return None
    try:
        value = _evaluate(ast.parse(expression, mode="eval"))
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError):
        return None
    shown = f"{value:,.10g}" if isinstance(value, float) and not value.is_integer() else f"{int(value):,}"
    return f"{match['sum'].strip()} = {shown}"


def asks_for_ips(text: str) -> bool:
    text = text or ""
    return bool(IP_QUESTION.search(text)) and bool(ABOUT_THE_SERVERS.search(text)) and not NOT_THE_SERVERS.search(text)


def ip_list(proxmox_status: str) -> str | None:
    found = re.findall(r"^(Host \S+|(?:CT|VM) \d+ \S+) \((\d+\.\d+\.\d+\.\d+)\)", proxmox_status, re.MULTILINE)
    if not found:
        return None
    return "\n".join(f"- {name}: {address}" for name, address in found)


def calendar_add(text: str) -> tuple[str, str] | None:
    """(what, when) for a plain "put X in my calendar <when>"; None without a when, or with more asked."""
    match = CALENDAR_ADD.search(text or "")
    if not match or MORE_THAN_ONE_THING.search(text) or not match["when"].strip():
        return None
    return match["what"].strip(), match["when"].strip()


def list_add(text: str) -> tuple[str, str] | None:
    """(items, list name) for a plain "add X to my <name> list"."""
    match = LIST_ADD.search(text or "")
    if not match or MORE_THAN_ONE_THING.search(text):
        return None
    return match["items"].strip(), match["list"].strip()


def cant_do(text: str) -> str | None:
    """A plain "I can't" for a request Arty has no way to carry out, or None."""
    return next((reply for pattern, reply in CANT_DO if pattern.search(text or "")), None)

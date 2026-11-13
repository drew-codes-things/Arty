import builtins
import importlib
import inspect
import io
import logging
import re
import sys

from fresh_answer import FRESH_ANSWER

log = logging.getLogger("brain.code_check")

PYTHON_BLOCK = re.compile(r"```(?:python3?|py|pycon)?[ \t]*\n(.*?)```", re.S | re.I)
REPL_PROMPT = re.compile(r"^[ \t]*(?:>>>|\.\.\.)[ \t]?", re.M)
STRING_LITERAL = re.compile(r"\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'")
COMMENT = re.compile(r"#[^\n]*")
DOTTED_CALL = re.compile(r"(?<![\w.])([A-Za-z_][\w.]*)\.([A-Za-z_]\w*)\s*\(")
CHAINED_CALL = re.compile(r"[)\]]\s*\.([A-Za-z_]\w*)\s*\(")
DEFINED = re.compile(r"^[ \t]*(?:async[ \t]+)?(?:def|class)[ \t]+(\w+)", re.M)
PLAIN_IMPORT = re.compile(r"^[ \t]*import[ \t]+([\w.]+(?:[ \t]+as[ \t]+\w+)?(?:[ \t]*,[ \t]*[\w.]+(?:[ \t]+as[ \t]+\w+)?)*)", re.M)
FROM_IMPORT = re.compile(r"^[ \t]*from[ \t]+([\w.]+)[ \t]+import[ \t]+\(?([\w, \t\n]+?)\)?[ \t]*$", re.M)
WORD = re.compile(r"\w+")

BUILTIN_TYPES = (str, bytes, bytearray, list, dict, set, frozenset, tuple, int, float, complex, bool, object, type, range,
                 memoryview, slice, BaseException, io.TextIOWrapper, io.BufferedReader, io.BufferedWriter, io.BytesIO,
                 io.StringIO)
PLAIN_PYTHON = frozenset(dir(builtins)).union(*(dir(kind) for kind in BUILTIN_TYPES))
NEVER_IMPORTED = frozenset({"antigravity", "this", "idlelib", "tkinter", "turtle", "turtledemo", "__hello__", "__phello__"})

REWRITE_NOTE = ("(I checked your code against the page you read, {url}: it calls {names}, which neither that page nor "
                "the search results show, so {pronoun} may not exist. Rewrite your whole answer using only what the "
                "page shows, plus plain Python. Where the page has no ready-made way to do something, build it from "
                "the pieces it does show, or say you couldn't confirm one exists. " + FRESH_ANSWER + ")")


def python_code(answer: str) -> str:
    blocks = [REPL_PROMPT.sub("", block) for block in PYTHON_BLOCK.findall(answer)]
    return COMMENT.sub("", STRING_LITERAL.sub('""', "\n".join(blocks)))


def standard_module(name: str):
    if name.split(".")[0] not in sys.stdlib_module_names or name.split(".")[0] in NEVER_IMPORTED:
        return None
    try:
        return importlib.import_module(name)
    except ImportError as error:
        log.debug("couldn't load %s to check code against it: %s", name, error)
        return None


def names_in_module(module) -> set[str]:
    names = set(dir(module))
    for name in dir(module):
        member = getattr(module, name, None)
        if inspect.isclass(member):
            names.update(dir(member))
    return names


def imported_names(code: str) -> tuple[set[str], dict[str, set[str]]]:
    bound, modules = set(), {}
    for clause in PLAIN_IMPORT.findall(code):
        for part in clause.split(","):
            module, _, alias = part.strip().partition(" as ")
            modules.setdefault(module.strip(), set())
            bound.add(alias.strip() or module.strip().split(".")[0])
    for module, names in FROM_IMPORT.findall(code):
        for part in names.split(","):
            name, _, alias = part.strip().partition(" as ")
            if name.strip():
                bound.add(alias.strip() or name.strip())
                modules.setdefault(module, set()).add(name.strip())
    return bound, modules


def imported_member(module, name: str):
    if hasattr(module, name) or not hasattr(module, "__path__"):
        return getattr(module, name, None)
    return standard_module(f"{module.__name__}.{name}")


def names_from_imports(modules: dict[str, set[str]]) -> set[str]:
    known = set()
    for name, members in modules.items():
        if (module := standard_module(name)) is None:
            continue
        known |= names_in_module(module)
        for member in filter(inspect.ismodule, (imported_member(module, member) for member in members)):
            known |= names_in_module(member)
    return known


def known_names(code: str, evidence: str) -> set[str]:
    bound, modules = imported_names(code)
    return (set(PLAIN_PYTHON) | bound | set(DEFINED.findall(code)) | set(WORD.findall(evidence.lower()))
            | names_from_imports(modules))


def is_known(owner: str, name: str, known: set[str]) -> bool:
    if name in known or name.lower() in known:
        return True
    module = standard_module(owner) if owner else None
    return module is not None and hasattr(module, name)


def unconfirmed_calls(answer: str, evidence: str) -> list[str]:
    code = python_code(answer)
    if not code.strip():
        return []
    known = known_names(code, evidence)
    called = DOTTED_CALL.findall(code) + [("", name) for name in CHAINED_CALL.findall(code)]
    unconfirmed = []
    for owner, name in called:
        if name not in unconfirmed and not is_known(owner, name, known):
            unconfirmed.append(name)
    return unconfirmed


def rewrite_note(url: str, names: list[str]) -> str:
    shown = " and ".join(f"`{name}()`" for name in names)
    return REWRITE_NOTE.format(url=url, names=shown, pronoun="it" if len(names) == 1 else "they")

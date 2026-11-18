import builtins
import io
import json
import logging
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from fresh_answer import FRESH_ANSWER
from module_catalogue import may_import

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
CATALOGUE_SCRIPT = Path(__file__).resolve().with_name("module_catalogue.py")
CATALOGUE_SECONDS = 15
STDERR_SHOWN_CHARS = 300

REWRITE_NOTE = ("(I checked your code against the page you read, {url}: it calls {names}, which neither that page nor "
                "the search results show, so {pronoun} may not exist. Rewrite your whole answer using only what the "
                "page shows, plus plain Python. Where the page has no ready-made way to do something, build it from "
                "the pieces it does show, or say you couldn't confirm one exists. " + FRESH_ANSWER + ")")


def python_code(answer: str) -> str:
    blocks = [REPL_PROMPT.sub("", block) for block in PYTHON_BLOCK.findall(answer)]
    return COMMENT.sub("", STRING_LITERAL.sub('""', "\n".join(blocks)))


class CatalogueUnavailable(RuntimeError):
    pass


def built_catalogue(names: set[str]) -> dict[str, dict]:
    wanted = sorted(name for name in names if may_import(name))
    if not wanted:
        return {}
    with tempfile.TemporaryDirectory(prefix="arty-module-catalogue-") as folder:
        try:
            listed = subprocess.run([sys.executable, "-I", str(CATALOGUE_SCRIPT)], input=json.dumps(wanted), capture_output=True,
                                    text=True, timeout=CATALOGUE_SECONDS, cwd=folder, check=False)
        except (subprocess.TimeoutExpired, OSError) as error:
            raise CatalogueUnavailable(f"couldn't list the names in {', '.join(wanted)}: {error}") from error
    if listed.returncode != 0:
        raise CatalogueUnavailable(f"listing the names in {', '.join(wanted)} exited with {listed.returncode}: "
                                   f"{listed.stderr[-STDERR_SHOWN_CHARS:].strip()}")
    try:
        return json.loads(listed.stdout)
    except ValueError as error:
        raise CatalogueUnavailable(f"listing the names in {', '.join(wanted)} gave no readable answer: {error}") from error


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


def modules_to_list(modules: dict[str, set[str]], called: list[tuple[str, str]]) -> set[str]:
    wanted = set(modules) | {owner for owner, _ in called if owner}
    return wanted | {f"{module}.{member}" for module, members in modules.items() for member in members}


def names_from_imports(modules: dict[str, set[str]], catalogue: dict[str, dict]) -> set[str]:
    known = set()
    for name, members in modules.items():
        if name not in catalogue:
            continue
        known.update(catalogue[name]["all"])
        for member in members:
            known.update(catalogue.get(f"{name}.{member}", {}).get("all", ()))
    return known


def known_names(code: str, evidence: str, modules: dict[str, set[str]], bound: set[str], catalogue: dict[str, dict]) -> set[str]:
    return (set(PLAIN_PYTHON) | bound | set(DEFINED.findall(code)) | set(WORD.findall(evidence.lower()))
            | names_from_imports(modules, catalogue))


def is_known(owner: str, name: str, known: set[str], catalogue: dict[str, dict]) -> bool:
    if name in known or name.lower() in known:
        return True
    return name in catalogue.get(owner, {}).get("own", ())


def unconfirmed_calls(answer: str, evidence: str) -> list[str]:
    code = python_code(answer)
    if not code.strip():
        return []
    called = DOTTED_CALL.findall(code) + [("", name) for name in CHAINED_CALL.findall(code)]
    bound, modules = imported_names(code)
    catalogue = built_catalogue(modules_to_list(modules, called))
    known = known_names(code, evidence, modules, bound, catalogue)
    unconfirmed = []
    for owner, name in called:
        if name not in unconfirmed and not is_known(owner, name, known, catalogue):
            unconfirmed.append(name)
    return unconfirmed


def rewrite_note(url: str, names: list[str]) -> str:
    shown = " and ".join(f"`{name}()`" for name in names)
    return REWRITE_NOTE.format(url=url, names=shown, pronoun="it" if len(names) == 1 else "they")

"""Loads character_config.yaml.

In a checkout the repo is both the asset root and the writable root. Inside
the packaged app (an AppImage, whose files are read-only) the two come apart:
the voice model, the avatar and the Python source ship in the bundle, while the config, the memory and the
chat history have to live somewhere the user can actually write. Electron
sets ARTY_ROOT and ARTY_DATA to keep the two apart; without them
everything falls back to the repo and development works as it always did.
"""
from pathlib import Path
import functools
import json
import os
import re
import threading

import yaml


def _env_path(name):
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else None


REPO_ROOT = _env_path("ARTY_ROOT") or Path(__file__).resolve().parents[2]
DATA_ROOT = _env_path("ARTY_DATA") or REPO_ROOT

CONFIG_PATH = DATA_ROOT / "arty_config.yaml"
DEFAULT_CONFIG = REPO_ROOT / "arty_config.example.yaml"
PRIVATE_FILE_MODE = 0o600


def write_private(path, text):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        if hasattr(os, "fchmod"):
            os.fchmod(handle.fileno(), PRIVATE_FILE_MODE)
        handle.write(text)


def _seed_config():
    """First run has no config, so start from the shipped defaults."""
    if not DEFAULT_CONFIG.exists():
        raise FileNotFoundError(
            f"No config at {CONFIG_PATH} and no defaults at {DEFAULT_CONFIG}."
        )
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_private(CONFIG_PATH, DEFAULT_CONFIG.read_text(encoding="utf-8"))
    print(f"[config] wrote a starter config to {CONFIG_PATH}", flush=True)


@functools.lru_cache(maxsize=1)
def load_config():
    if not CONFIG_PATH.exists():
        _seed_config()
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


_lock = threading.Lock()


_value_overrides = {}


def set_value(section, key, value):
    """Change one plain setting now and in the config file. The section is
    added at the end if it isn't there."""
    with _lock:
        text = CONFIG_PATH.read_text(encoding="utf-8")
        literal = "true" if value is True else "false" if value is False else json.dumps(value)
        changed = _with_value(_in_block_style(text, section), section, key, literal)
        if _read_back(changed, section, key) != value:
            raise ValueError(f"couldn't set {section}.{key} in {CONFIG_PATH}; change it there by hand")
        tmp = CONFIG_PATH.with_suffix(".yaml.tmp")
        write_private(tmp, changed)
        os.replace(tmp, CONFIG_PATH)
        _value_overrides[(section, key)] = value


def _read_back(text, section, key):
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise ValueError(f"{CONFIG_PATH} isn't valid YAML after the change ({error}); change it there by hand") from None
    found = loaded.get(section) if isinstance(loaded, dict) else None
    return found.get(key) if isinstance(found, dict) else None


def _in_block_style(text, section):
    flow = re.compile(rf"^{re.escape(section)}:[ \t]*(?P<mapping>\{{.*\}})[ \t]*(?:#.*)?$")
    lines = text.split("\n")
    for number, line in enumerate(lines):
        found = flow.match(line)
        if not found:
            continue
        try:
            mapping = yaml.safe_load(found.group("mapping"))
        except yaml.YAMLError as error:
            raise ValueError(f"couldn't read the {section} section of {CONFIG_PATH} ({error}); change it there by hand") from None
        block = [f"{section}:"] + [f"{' ' * DEFAULT_INDENT}{name}: {json.dumps(value)}" for name, value in (mapping or {}).items()]
        return "\n".join(lines[:number] + block + lines[number + 1:])
    return text


_VALUE_THEN_COMMENT = re.compile(r"""(?P<value>"(?:[^"\\]|\\.)*"|'(?:[^']|'')*'|.*?)(?P<comment>[ \t]+#.*)?$""")
DEFAULT_INDENT = 2


def _section_span(lines, section):
    header = re.compile(rf"^{re.escape(section)}:[ \t]*(?:#.*)?$")
    for start, line in enumerate(lines):
        if header.match(line):
            end = start + 1
            while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t#"):
                end += 1
            return start, end
    return None


def _with_value(text, section, key, literal):
    lines = text.split("\n")
    span = _section_span(lines, section)
    if span is None:
        return text.rstrip() + f"\n\n{section}:\n  {key}: {literal}\n"
    start, end = span
    indents = [len(line) - len(line.lstrip(" ")) for line in lines[start + 1:end]
               if line.strip() and not line.lstrip().startswith("#")]
    indent = " " * (min(indents) if indents else DEFAULT_INDENT)
    key_line = re.compile(rf"^{indent}{re.escape(key)}:(?P<rest>.*)$")
    for number in range(start + 1, end):
        found = key_line.match(lines[number])
        if found:
            comment = _VALUE_THEN_COMMENT.match(found.group("rest").strip()).group("comment") or ""
            continued = _value_lines_after(lines, number + 1, end, indent)
            lines[number:number + 1 + continued] = [f"{indent}{key}: {literal}{comment}"]
            return "\n".join(lines)
    lines.insert(start + 1, f"{indent}{key}: {literal}")
    return "\n".join(lines)


def _value_lines_after(lines, first, end, indent):
    count = 0
    for line in lines[first:end]:
        stripped = line.strip()
        deeper = len(line) - len(line.lstrip(" ")) > len(indent)
        listed_here = line.startswith(f"{indent}- ") or line == f"{indent}-"
        if stripped and not (deeper or listed_here):
            break
        count += 1
    while count and not lines[first + count - 1].strip():
        count -= 1
    return count


def setting(section, key, default=None):
    """A setting, including any changed while running (see set_value)."""
    if (section, key) in _value_overrides:
        return _value_overrides[(section, key)]
    return (load_config().get(section) or {}).get(key, default)


def resolve(path_str):
    """A read-only asset that ships with the app."""
    p = Path(path_str)
    return p if p.is_absolute() else (REPO_ROOT / p)


def resolve_data(path_str):
    """A file the app writes to. Inside the bundle this is Application Support."""
    p = Path(path_str)
    if p.is_absolute():
        return p
    p = DATA_ROOT / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

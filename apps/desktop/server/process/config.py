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
        _value_overrides[(section, key)] = value
        text = CONFIG_PATH.read_text(encoding="utf-8")
        literal = {True: "true", False: "false"}.get(value, json.dumps(value))
        # The value, keeping whatever comment follows it and the space before.
        pattern = (rf"(^{re.escape(section)}:\n(?:[ \t]+.*\n|\n)*?[ \t]+{re.escape(key)}:)"
                   r"[ \t]*[^\n#]*?([ \t]*(?:#.*)?)$")
        if re.search(pattern, text, re.M):
            text = re.sub(pattern, rf"\g<1> {literal}\g<2>", text, count=1, flags=re.M)
        elif re.search(rf"^{re.escape(section)}:\s*$", text, re.M):
            text = re.sub(rf"^({re.escape(section)}:\s*)$", rf"\g<1>\n  {key}: {literal}",
                          text, count=1, flags=re.M)
        else:
            text = text.rstrip() + f"\n\n{section}:\n  {key}: {literal}\n"
        tmp = CONFIG_PATH.with_suffix(".yaml.tmp")
        write_private(tmp, text)
        os.replace(tmp, CONFIG_PATH)


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

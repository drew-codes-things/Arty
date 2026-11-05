"""set_value should change one setting in place, add it to its section, or
add the section, and leave everything else in the file alone."""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from process import config

BASE = """llm:
  model: "arty"   # the brain

computer:
  enabled: false   # off by default

tools:
  timers: true
"""


def write(tmp_path, monkeypatch, text):
    path = tmp_path / "character_config.yaml"
    path.write_text(text)
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.setattr(config, "_value_overrides", {})
    return path


def test_changes_a_value_in_place(tmp_path, monkeypatch):
    path = write(tmp_path, monkeypatch, BASE)
    config.set_value("computer", "enabled", True)
    text = path.read_text()
    assert yaml.safe_load(text)["computer"]["enabled"] is True
    assert '  model: "arty"   # the brain' in text
    assert yaml.safe_load(text)["tools"] == {"timers": True}


def test_adds_a_missing_key_and_section(tmp_path, monkeypatch):
    path = write(tmp_path, monkeypatch, BASE)
    config.set_value("tools", "computer", False)
    config.set_value("wake", "listen_seconds", 12)
    d = yaml.safe_load(path.read_text())
    assert d["tools"] == {"timers": True, "computer": False}
    assert d["wake"] == {"listen_seconds": 12}
    assert d["computer"]["enabled"] is False


def test_the_config_stays_readable_by_this_user_only(tmp_path, monkeypatch):
    path = write(tmp_path, monkeypatch, BASE)
    path.chmod(0o644)
    config.set_value("computer", "enabled", True)
    assert path.stat().st_mode & 0o777 == 0o600


def test_a_seeded_config_is_readable_by_this_user_only(tmp_path, monkeypatch):
    starter = tmp_path / "example.yaml"
    starter.write_text(BASE)
    starter.chmod(0o644)
    monkeypatch.setattr(config, "DEFAULT_CONFIG", starter)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "data" / "arty_config.yaml")
    config._seed_config()
    assert config.CONFIG_PATH.stat().st_mode & 0o777 == 0o600

"""set_value should change one setting in place, add it to its section, or
add the section, and leave everything else in the file alone."""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from process import config

NO_PERMISSION_BITS = pytest.mark.skipif(sys.platform == "win32", reason="Windows has no Unix permission bits; the file sits in the user's own profile folder")

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


@NO_PERMISSION_BITS
def test_the_config_stays_readable_by_this_user_only(tmp_path, monkeypatch):
    path = write(tmp_path, monkeypatch, BASE)
    path.chmod(0o644)
    config.set_value("computer", "enabled", True)
    assert path.stat().st_mode & 0o777 == 0o600


@NO_PERMISSION_BITS
def test_a_seeded_config_is_readable_by_this_user_only(tmp_path, monkeypatch):
    starter = tmp_path / "example.yaml"
    starter.write_text(BASE)
    starter.chmod(0o644)
    monkeypatch.setattr(config, "DEFAULT_CONFIG", starter)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "data" / "arty_config.yaml")
    config._seed_config()
    assert config.CONFIG_PATH.stat().st_mode & 0o777 == 0o600


def test_a_value_with_a_hash_in_it_is_replaced_whole(tmp_path, monkeypatch):
    path = write(tmp_path, monkeypatch, 'brain:\n  model: "arty#2"   # the brain\n  api_key: "x"\n')
    config.set_value("brain", "model", "arty/qwen")
    text = path.read_text()
    assert yaml.safe_load(text)["brain"] == {"model": "arty/qwen", "api_key": "x"}
    assert '  model: "arty/qwen"   # the brain\n' in text
    path.write_text("brain:\n  model: arty#2   # the brain\n")
    config.set_value("brain", "model", "q")
    assert path.read_text() == 'brain:\n  model: "q"   # the brain\n'


def test_a_key_after_a_comment_line_in_its_section_is_changed_not_duplicated(tmp_path, monkeypatch):
    path = write(tmp_path, monkeypatch, "computer:\n# the switch below\n  enabled: false\n\ntools:\n  timers: true\n")
    config.set_value("computer", "enabled", True)
    text = path.read_text()
    assert text.count("enabled:") == 1
    assert yaml.safe_load(text)["computer"]["enabled"] is True


def test_a_nested_key_with_the_same_name_is_left_alone(tmp_path, monkeypatch):
    path = write(tmp_path, monkeypatch, "tts:\n  kokoro:\n    speed: 1.0\n  speed: 2\n")
    config.set_value("tts", "speed", 3)
    assert yaml.safe_load(path.read_text())["tts"] == {"kokoro": {"speed": 1.0}, "speed": 3}


def test_a_list_value_is_written_and_read_back(tmp_path, monkeypatch):
    path = write(tmp_path, monkeypatch, BASE)
    config.set_value("browser", "allowed_sites", ["shop.example", "mail.example.com"])
    assert yaml.safe_load(path.read_text())["browser"]["allowed_sites"] == ["shop.example", "mail.example.com"]
    config.set_value("browser", "allowed_sites", [])
    assert yaml.safe_load(path.read_text())["browser"]["allowed_sites"] == []

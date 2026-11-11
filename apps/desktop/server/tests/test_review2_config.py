import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import yaml

from process import browser, config


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "arty_config.yaml"
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.setattr(config, "_value_overrides", {})
    config.load_config.cache_clear()
    yield path
    config.load_config.cache_clear()


def test_a_refused_write_leaves_the_running_setting_alone(config_file):
    config_file.write_text("computer:\n  enabled: false\n  enabled: false\n")
    with pytest.raises(ValueError):
        config.set_value("computer", "enabled", True)
    assert config.setting("computer", "enabled") is False


def test_a_hand_written_block_list_is_replaced_not_left_dangling(config_file):
    config_file.write_text("browser:\n  allowed_sites:\n  - a.example\n  - b.example\n  other: 1\n")
    config.set_value("browser", "allowed_sites", ["a.example", "c.example"])
    loaded = yaml.safe_load(config_file.read_text())
    assert loaded["browser"] == {"allowed_sites": ["a.example", "c.example"], "other": 1}


def test_a_more_indented_block_list_is_replaced_too(config_file):
    config_file.write_text("browser:\n  allowed_sites:\n    - a.example\n  other: 1\n")
    config.set_value("browser", "allowed_sites", [])
    assert yaml.safe_load(config_file.read_text())["browser"] == {"allowed_sites": [], "other": 1}


def test_a_file_that_will_not_parse_is_a_value_error_not_a_yaml_one(config_file):
    config_file.write_text("browser:\n  allowed_sites: []\nother: [x\n")
    with pytest.raises(ValueError) as raised:
        config.set_value("browser", "allowed_sites", ["b.example"])
    assert not isinstance(raised.value, yaml.YAMLError)


def test_a_section_written_in_flow_style_is_changed_not_added_twice(config_file):
    config_file.write_text("browser: {}\ncomputer: {enabled: true}\n")
    config.set_value("browser", "allowed_sites", ["a.example"])
    config.set_value("computer", "mouse", False)
    text = config_file.read_text()
    assert text.count("browser:") == 1 and text.count("computer:") == 1
    assert yaml.safe_load(text) == {"browser": {"allowed_sites": ["a.example"]}, "computer": {"enabled": True, "mouse": False}}


def test_allowed_sites_written_as_one_name_is_one_site(config_file):
    config_file.write_text("browser:\n  allowed_sites: shop.example\n")
    assert browser.allowed_sites() == ["shop.example"]
    assert not browser.site_allowed("s")


def test_a_yes_that_cannot_be_saved_still_lets_the_step_run(config_file, monkeypatch, capsys):
    config_file.write_text("browser:\n  allowed_sites: []\n")
    browser._asked[browser._call_key("chrome_read", {})] = "shop.example"
    monkeypatch.setattr(config, "set_value", lambda *args: (_ for _ in ()).throw(ValueError("read-only")))
    browser.remember_consent("chrome_read", {})
    assert "shop.example" in capsys.readouterr().out

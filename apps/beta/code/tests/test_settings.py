import os
import stat
import sys

import pytest

from arty_code import settings


def test_a_fresh_install_uses_the_coder_model_and_is_not_ready(tmp_path):
    loaded = settings.load(tmp_path / "missing.json", environ={})
    assert loaded.model == settings.DEFAULT_MODEL
    assert not loaded.is_ready()


def test_the_environment_overrides_the_config_file(tmp_path):
    path = tmp_path / "config.json"
    settings.save(settings.Settings(base_url="http://brain.lan:8081/v1", api_key="from-file"), path)
    loaded = settings.load(path, environ={"ARTY_API_KEY": "from-env", "ARTY_CODE_MODEL": "arty/other"})
    assert (loaded.base_url, loaded.api_key, loaded.model) == ("http://brain.lan:8081/v1", "from-env", "arty/other")
    assert loaded.is_ready()


def test_a_remote_address_alone_is_enough(tmp_path):
    loaded = settings.load(tmp_path / "none.json", environ={"ARTY_REMOTE_URL": "https://arty.example/v1", "ARTY_API_KEY": "k"})
    assert loaded.is_ready()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_the_saved_config_is_private(tmp_path):
    path = settings.save(settings.Settings(api_key="secret"), tmp_path / "deep" / "config.json")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_windows_keeps_the_config_in_appdata():
    folder = settings.config_dir(environ={"APPDATA": r"C:\Users\me\AppData\Roaming"}, platform="win32")
    assert str(folder).endswith("arty-code")
    assert "Roaming" in str(folder)


def test_linux_follows_xdg_config_home():
    folder = settings.config_dir(environ={"XDG_CONFIG_HOME": "/tmp/cfg"}, platform="linux")
    assert str(folder) == os.path.join("/tmp/cfg", "arty-code")


def test_a_broken_config_file_says_where_it_is(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json")
    with pytest.raises(settings.SettingsError, match=str(path).replace("\\", "\\\\")):
        settings.load(path, environ={})

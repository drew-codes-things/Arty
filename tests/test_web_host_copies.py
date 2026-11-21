import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BRAIN_COPY = ROOT / "brain" / "web_host.py"
OTHER_COPIES = (ROOT / "hub" / "web_host.py", ROOT / "arty-pc" / "web_host.py")
OWN_HOSTS = ("drive.example.org", "calendar.example.org", "starfire.example.org")
OWN_APP_ADDRESSES = (
    "https://drive.example.org/", "https://DRIVE.example.org/", "https://drive.example.org./",
    "https://drive.example.org:8443/", "http://drive.example.org/",
    "https://x.drive.example.org/", "https://ｄrive.example.org/", "https://drive。example。org/",
    "https://drive．example.org/", "https://drive%2Eexample.org/", "ws://drive.example.org/",
)
OTHER_ADDRESSES = (
    "https://notdrive.example.org/", "https://drive.example.org.evil.example/",
    "https://evil.example/?next=https://drive.example.org", "https://[::ffff:203.0.113.5]/", "",
)
UNCLEAR_ADDRESSES = (
    "https://drive.example.org\\@evil.example/x", "https:\\\\drive.example.org/", "https://drive.example.org\\api/x",
    "https://dri\tve.example.org/", "https://drive.example.org/\x00", "https://drive.example.org\x7f/",
    "https://user@drive.example.org/", "https://drive.example.org%2f@evil.example/", "https:drive.example.org",
)


def loaded(path):
    spec = importlib.util.spec_from_file_location(f"web_host_{path.parent.name.replace('-', '_')}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_hub_and_the_desktop_agent_carry_the_brains_own_copy():
    for copy in OTHER_COPIES:
        assert copy.read_bytes() == BRAIN_COPY.read_bytes(), copy


@pytest.mark.parametrize("url", OWN_APP_ADDRESSES)
def test_every_way_of_writing_an_own_app_is_an_own_app(url):
    assert loaded(BRAIN_COPY).is_own_app_address(url, OWN_HOSTS)


@pytest.mark.parametrize("url", OTHER_ADDRESSES)
def test_other_hosts_are_not_own_apps(url):
    assert not loaded(BRAIN_COPY).is_own_app_address(url, OWN_HOSTS)


@pytest.mark.parametrize("url", UNCLEAR_ADDRESSES)
def test_a_backslash_or_control_character_is_refused(url):
    web_host = loaded(BRAIN_COPY)
    with pytest.raises(web_host.UnclearAddress):
        web_host.is_own_app_address(url, OWN_HOSTS)


def test_listed_hosts_are_read_the_same_way():
    web_host = loaded(BRAIN_COPY)
    assert web_host.is_own_app_address("https://drive.example.org/", ["https://ｄrive.example.org"[8:]])
    assert web_host.canonical_host("Bücher.example") == "xn--bcher-kva.example"
    with pytest.raises(web_host.UnclearAddress):
        web_host.canonical_host("*.example.org")

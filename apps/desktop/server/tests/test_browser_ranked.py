import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from process import browser, config
from process.tools import computer, registry

VIDEO_SITE = "https://video.example/results?q=lofi"
VIDEO_TAB = {"tabs": [{"id": 7, "active": True, "title": "lofi - Video site", "url": VIDEO_SITE}]}


@pytest.fixture
def sites(tmp_path, monkeypatch):
    path = tmp_path / "arty_config.yaml"
    path.write_text('browser:\n  allowed_sites: ["video.example"]\n')
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.setattr(config, "_value_overrides", {})
    config.load_config.cache_clear()
    browser._asked.clear()
    yield path
    config.load_config.cache_clear()


def extension(*replies):
    browser.next_command(0)
    seen = []

    def answer():
        for reply in replies:
            command = browser.next_command(5)
            seen.append(command)
            browser.put_result(command["id"], reply)
    threading.Thread(target=answer, daemon=True).start()
    return seen


def item(n, label, group, kind="link", to=""):
    return {"n": n, "kind": kind, "label": label, "group": group, "to": to}


def results_page():
    menu = [item(n, f"Menu entry {n}", "menu") for n in range(1, 41)]
    return {"tab": 7, "title": "lofi - Video site", "url": VIDEO_SITE, "text": "lofi results",
            "items": [*menu,
                      item(41, "Search", "menu", kind="field"),
                      item(42, "Delete my account", "menu", kind="button"),
                      item(50, "Sponsored: buy beats", "ad"),
                      item(60, "Lofi Topic channel", "result", to="channel"),
                      item(62, "lofi hip hop radio", "result", to="video"),
                      item(63, "Lofi Girl", "main", to="channel"),
                      item(64, "Action menu", "main", kind="button"),
                      item(70, "Beats to sleep to", "result", to="video"),
                      item(138, "Tiny lofi short", "short", to="short video"),
                      item(139, "Another lofi short", "short", to="short video"),
                      item(200, "Accept all", "dialog", kind="button")]}


def test_a_read_puts_the_main_results_first_and_leaves_out_menus_ads_and_shorts(sites):
    extension(results_page())
    page = browser.chrome_read()
    listing = page.split(browser._UNTRUSTED)[0]
    assert listing.index("[62] video: lofi hip hop radio") < listing.index("[63] link: Lofi Girl")
    assert "[60] channel: Lofi Topic channel" in listing
    assert listing.index("Main results") < listing.index("[60]") < listing.index("[62]") < listing.index("[70]")
    for left_out in ("Menu entry 5", "Sponsored", "Tiny lofi short", "Delete my account"):
        assert left_out not in listing, left_out
    assert "41 menu and sidebar items" in listing
    assert "2 short videos" in listing and "1 ad" in listing and "chrome_find" in listing
    assert "[41] field: Search" in listing
    assert listing.index("[200] button: Accept all") < listing.index("Main results")


def test_a_page_from_an_older_extension_still_lists_its_items(sites):
    extension({"tab": 7, "title": "Shop", "url": VIDEO_SITE, "text": "", "items": [{"n": 1, "kind": "button", "label": "Buy now"}]})
    assert "[1] button: Buy now" in browser.chrome_read()


def test_a_left_out_item_is_still_asked_about_before_it_is_clicked(sites):
    extension(results_page())
    browser.chrome_read()
    assert "Delete my account" in browser.needs_ok("chrome_click", {"item": 42})


def test_find_lists_matching_items_from_any_part_of_the_page(sites):
    seen = extension(results_page())
    found = browser.chrome_find("lofi short")
    assert seen[0]["action"] == "read"
    assert "[138] short video: Tiny lofi short" in found and "[139] short video: Another lofi short" in found
    assert "lofi hip hop radio" not in found


def test_find_says_plainly_when_nothing_matches(sites):
    extension(results_page())
    assert "Nothing" in browser.chrome_find("podcast")


def test_find_is_offered_with_the_browser_tools_and_asks_about_new_sites(sites, monkeypatch):
    monkeypatch.setattr(computer, "available", lambda: True)
    monkeypatch.setattr(browser, "connected", lambda: True)
    offered = {tool["function"]["name"] for tool in registry.definitions("search youtube for lofi and play the first result")}
    assert "chrome_find" in offered
    assert "chrome_find" in browser.SITE_TOOLS and "chrome_find" in browser.LOOK_ONLY_TOOLS
    assert registry.describe("chrome_find", '{"words": "sign in"}')[0] == "looking for sign in on the page"


def clicked_onto(url):
    return {"clicked": "lofi hip hop radio", "title": "lofi hip hop radio - Video site", "url": url,
            "media": [{"kind": "video", "playing": False, "muted": False, "at": 0, "length": 30}]}


def test_a_click_that_leads_to_a_video_says_whether_it_is_playing(sites):
    extension(VIDEO_TAB, clicked_onto("https://video.example/watch?v=1"))
    result = browser.chrome_click(item=62)
    assert "Now on: lofi hip hop radio - Video site (https://video.example/watch?v=1)." in result
    assert "Video: not playing, 0:00 of 0:30." in result


def test_a_click_onto_a_video_says_what_to_tell_the_user_and_to_click_nothing_else(sites):
    extension(VIDEO_TAB, clicked_onto("https://video.example/watch?v=1"))
    result = browser.chrome_click(item=62)
    last_line = result.splitlines()[-1]
    assert "lofi hip hop radio - Video site" in last_line
    assert "one or two sentences" in last_line and "whether it is playing" in last_line
    assert "subscribe" in last_line.lower()


def test_a_click_onto_a_page_without_a_video_adds_no_video_advice(sites):
    extension(VIDEO_TAB, {"clicked": "About", "title": "About - Video site", "url": "https://video.example/about"})
    result = browser.chrome_click(item=3)
    assert "one or two sentences" not in result


def test_a_click_onto_a_site_not_allowed_yet_says_nothing_about_its_players(sites):
    extension(VIDEO_TAB, clicked_onto("https://other.example/watch?v=1"))
    result = browser.chrome_click(item=62)
    assert "haven't allowed" in result and "Video:" not in result


def test_a_video_that_is_still_loading_is_reported_as_starting_so_it_is_not_clicked_again():
    line = browser._player_line({"kind": "video", "playing": False, "starting": True, "at": 0, "length": 600})
    assert line == "Video: starting to play, 0:00 of 10:00."

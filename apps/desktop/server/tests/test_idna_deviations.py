import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from process import browser, link_browser, own_browser

DEVIATIONS = ["https://paypaß.example/login", "https://ς.example/", "https://pay‍pal.example/",
              "https://pay‌pal.example/", "https://PAYPAß.example/"]


@pytest.mark.parametrize("url", DEVIATIONS, ids=["sharp s", "final sigma", "zero width joiner", "zero width non-joiner",
                                                 "capitals and sharp s"])
def test_a_site_name_that_browsers_and_python_spell_differently_is_refused(url):
    assert browser.site_of(url) == ""
    assert "spell differently" in browser.address_problem(url)
    assert own_browser.arty_browser_open(url).startswith("Couldn't")
    with pytest.raises(link_browser.OpenRefused):
        link_browser.checked_address(url)


def test_other_international_names_are_still_shown_the_way_the_browser_reads_them():
    assert browser.site_of("https://bücher.example/") == "xn--bcher-kva.example"
    assert browser.address_problem("https://bücher.example/") is None

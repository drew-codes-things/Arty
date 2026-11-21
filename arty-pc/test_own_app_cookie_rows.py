import os
import sqlite3
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).with_name("own-app-cookie-rows")
OWN_HOSTS = ["drive.example.org", "calendar.example.org", "starfire.example.org"]


def profile_with(home: Path, host_keys: list[str], folder: str = "Default/Network", name: str = "session") -> None:
    cookies = home / ".browser-profile" / folder / "Cookies"
    cookies.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(cookies) as database:
        database.execute("CREATE TABLE IF NOT EXISTS cookies (host_key TEXT, name TEXT, value TEXT)")
        database.executemany("INSERT INTO cookies VALUES (?, ?, 'x')", [(key, name) for key in host_keys])


def counted(home: Path, hosts=OWN_HOSTS) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", "-", *hosts], input=SCRIPT.read_text(), capture_output=True, text=True,
                          env={**os.environ, "HOME": str(home)}, timeout=60)


def test_own_app_and_parent_domain_rows_are_counted(tmp_path):
    profile_with(tmp_path, ["drive.example.org", ".calendar.example.org", ".example.org", "x.starfire.example.org",
                            "news.example.com", ".example.com", "example.org", "notdrive.example.org"])
    done = counted(tmp_path)
    assert (done.returncode, done.stdout.strip()) == (0, "4"), done.stderr


def test_a_clean_profile_counts_nothing(tmp_path):
    profile_with(tmp_path, ["news.example.com", ".google.com", "example.org"])
    profile_with(tmp_path, ["accounts.google.com"], folder="Default")
    done = counted(tmp_path)
    assert (done.returncode, done.stdout.strip()) == (0, "0"), done.stderr


def test_every_profile_folder_is_counted(tmp_path):
    profile_with(tmp_path, ["drive.example.org"], folder="Default")
    profile_with(tmp_path, [".starfire.example.org"], folder="Profile 1/Network")
    assert counted(tmp_path).stdout.strip() == "2"


def test_no_profile_counts_nothing(tmp_path):
    done = counted(tmp_path)
    assert (done.returncode, done.stdout.strip()) == (0, "0"), done.stderr


def test_an_unreadable_cookie_file_fails_instead_of_counting_nothing(tmp_path):
    cookies = tmp_path / ".browser-profile" / "Default" / "Cookies"
    cookies.parent.mkdir(parents=True)
    cookies.write_bytes(b"not a database")
    done = counted(tmp_path)
    assert done.returncode != 0 and done.stdout.strip() == ""


def test_cloudflare_s_own_cookies_on_the_parent_domain_are_not_counted(tmp_path):
    for name in ("__cf_bm", "cf_clearance", "_cfuvid", "__cflb", "__cfruid"):
        profile_with(tmp_path, [".example.org"], name=name)
    done = counted(tmp_path)
    assert (done.returncode, done.stdout.strip()) == (0, "0"), done.stderr


def test_any_other_parent_domain_cookie_and_every_own_app_cookie_still_count(tmp_path):
    profile_with(tmp_path, [".example.org"], name="CF_Authorization")
    profile_with(tmp_path, [".example.org"], name="session_id")
    profile_with(tmp_path, ["drive.example.org", ".starfire.example.org"], name="__cf_bm")
    profile_with(tmp_path, [".example.org"], name="__CF_BM")
    done = counted(tmp_path)
    assert (done.returncode, done.stdout.strip()) == (0, "5"), done.stderr

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hub"))

from accounts import AccountError, AccountStore

GOOD = "correct horse battery"


def test_first_account_is_created_once(tmp_path):
    store = AccountStore(str(tmp_path))
    assert not store.has_accounts()
    assert store.create_first(" Sam@Example.com ", GOOD, GOOD) == "sam@example.com"
    assert store.has_accounts()
    assert oct((tmp_path / "accounts.json").stat().st_mode & 0o777) == "0o600"
    assert GOOD not in (tmp_path / "accounts.json").read_text()
    with pytest.raises(AccountError):
        store.create_first("other@example.com", GOOD, GOOD)


@pytest.mark.parametrize("email, password, confirm", [
    ("not-an-email", GOOD, GOOD),
    ("sam@example.com", "short", "short"),
    ("sam@example.com", GOOD, GOOD + "x"),
])
def test_bad_setup_is_refused(tmp_path, email, password, confirm):
    store = AccountStore(str(tmp_path))
    with pytest.raises(AccountError):
        store.create_first(email, password, confirm)
    assert not store.has_accounts()


def test_verify(tmp_path):
    store = AccountStore(str(tmp_path))
    store.create_first("sam@example.com", GOOD, GOOD)
    assert store.verify("SAM@example.com", GOOD)
    assert not store.verify("sam@example.com", "wrong password")
    assert not store.verify("someone@example.com", GOOD)


@pytest.mark.parametrize("password", ["x" * 100, "é" * 60, "long passphrase " * 15])
def test_passphrases_longer_than_bcrypt_can_take_still_work(tmp_path, password):
    store = AccountStore(str(tmp_path))
    store.create_first("sam@example.com", password, password)
    assert store.verify("sam@example.com", password)
    assert not store.verify("sam@example.com", password[:-1] + "!")


def test_a_long_passphrase_is_checked_beyond_the_first_72_bytes(tmp_path):
    store = AccountStore(str(tmp_path))
    password = "a" * 80
    store.create_first("sam@example.com", password, password)
    assert not store.verify("sam@example.com", "a" * 72 + "b" * 8)


def test_a_wrong_long_password_for_an_unknown_email_is_refused_not_an_error(tmp_path):
    store = AccountStore(str(tmp_path))
    store.create_first("sam@example.com", GOOD, GOOD)
    assert not store.verify("nobody@example.com", "z" * 200)


def test_accounts_made_before_the_change_still_log_in(tmp_path):
    import json
    import bcrypt
    (tmp_path / "accounts.json").write_text(json.dumps(
        [{"email": "sam@example.com", "password_hash": bcrypt.hashpw(GOOD.encode(), bcrypt.gensalt(4)).decode()}]))
    store = AccountStore(str(tmp_path))
    assert store.verify("sam@example.com", GOOD)
    assert not store.verify("sam@example.com", GOOD + "x" * 80)

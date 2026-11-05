import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hub"))

from accounts import AccountError, AccountStore

GOOD = "correct horse battery"


def test_first_account_is_created_once(tmp_path):
    store = AccountStore(str(tmp_path))
    assert not store.has_accounts()
    assert store.create_first(" Drew@Example.com ", GOOD, GOOD) == "drew@example.com"
    assert store.has_accounts()
    assert oct((tmp_path / "accounts.json").stat().st_mode & 0o777) == "0o600"
    assert GOOD not in (tmp_path / "accounts.json").read_text()
    with pytest.raises(AccountError):
        store.create_first("other@example.com", GOOD, GOOD)


@pytest.mark.parametrize("email, password, confirm", [
    ("not-an-email", GOOD, GOOD),
    ("drew@example.com", "short", "short"),
    ("drew@example.com", GOOD, GOOD + "x"),
])
def test_bad_setup_is_refused(tmp_path, email, password, confirm):
    store = AccountStore(str(tmp_path))
    with pytest.raises(AccountError):
        store.create_first(email, password, confirm)
    assert not store.has_accounts()


def test_verify(tmp_path):
    store = AccountStore(str(tmp_path))
    store.create_first("drew@example.com", GOOD, GOOD)
    assert store.verify("DREW@example.com", GOOD)
    assert not store.verify("drew@example.com", "wrong password")
    assert not store.verify("someone@example.com", GOOD)

from __future__ import annotations

import json
import os
import re
import threading

import bcrypt

from devices import write_private_json

MIN_PASSWORD_CHARS = 10
MAX_FIELD_CHARS = 256
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
UNUSED_HASH = bcrypt.hashpw(b"my-voice-timing-equaliser", bcrypt.gensalt()).decode()


class AccountError(ValueError):
    pass


def check_new_password(password: str, confirm: str) -> None:
    if len(password) < MIN_PASSWORD_CHARS:
        raise AccountError(f"use at least {MIN_PASSWORD_CHARS} characters")
    if len(password) > MAX_FIELD_CHARS:
        raise AccountError("that password is too long")
    if password != confirm:
        raise AccountError("the two passwords don't match")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


class AccountStore:

    def __init__(self, data_dir: str):
        self.path = os.path.join(data_dir, "accounts.json")
        self._lock = threading.Lock()

    def _load(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def has_accounts(self) -> bool:
        return bool(self._load())

    def create_first(self, email: str, password: str, confirm: str) -> str:
        email = email.strip().lower()
        if not EMAIL.match(email) or len(email) > MAX_FIELD_CHARS:
            raise AccountError("enter a valid email address")
        check_new_password(password, confirm)
        with self._lock:
            if self._load():
                raise AccountError("an account already exists - log in instead")
            write_private_json(self.path, [{"email": email, "password_hash": hash_password(password)}])
        return email

    def verify(self, email: str, password: str) -> bool:
        account = next((a for a in self._load() if a["email"] == email.strip().lower()), None)
        stored = account["password_hash"] if account else UNUSED_HASH
        matches = bcrypt.checkpw(password[:MAX_FIELD_CHARS].encode(), stored.encode())
        return matches and account is not None

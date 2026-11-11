from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading

import bcrypt

from devices import write_private_json

MIN_PASSWORD_CHARS = 10
MAX_FIELD_CHARS = 256
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PREHASHED = "sha256-bcrypt$"
BCRYPT_MAX_BYTES = 72


class AccountError(ValueError):
    pass


def check_new_password(password: str, confirm: str) -> None:
    if len(password) < MIN_PASSWORD_CHARS:
        raise AccountError(f"use at least {MIN_PASSWORD_CHARS} characters")
    if len(password) > MAX_FIELD_CHARS:
        raise AccountError("that password is too long")
    if password != confirm:
        raise AccountError("the two passwords don't match")


def prehashed(password: str) -> bytes:
    return base64.b64encode(hashlib.sha256(password.encode()).digest())


def hash_password(password: str) -> str:
    return PREHASHED + bcrypt.hashpw(prehashed(password), bcrypt.gensalt()).decode()


def password_matches(password: str, stored: str) -> bool:
    if stored.startswith(PREHASHED):
        return bcrypt.checkpw(prehashed(password), stored.removeprefix(PREHASHED).encode())
    return bcrypt.checkpw(password.encode()[:BCRYPT_MAX_BYTES], stored.encode())


UNUSED_HASH = hash_password("my-voice-timing-equaliser")


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
        matches = password_matches(password[:MAX_FIELD_CHARS], stored)
        return matches and account is not None

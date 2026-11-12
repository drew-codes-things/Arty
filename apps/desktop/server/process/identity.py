import hashlib
import hmac
import secrets

IDENTITY_CONTEXT = "arty-identity:"
NONCE_BYTES = 16


def new_nonce() -> str:
    return secrets.token_hex(NONCE_BYTES)


def proof(key: str, nonce: str) -> str:
    return hmac.new(key.encode(), f"{IDENTITY_CONTEXT}{nonce}".encode(), hashlib.sha256).hexdigest()


def accepted_proofs(key: str, nonce: str) -> list[str]:
    if not key:
        return []
    return [proof(key, nonce), proof(hashlib.sha256(key.encode()).hexdigest(), nonce)]


def is_proven(proofs, key: str, nonce: str) -> bool:
    if not isinstance(proofs, list):
        return False
    accepted = accepted_proofs(key, nonce)
    return any(isinstance(given, str) and hmac.compare_digest(given.encode(), expected.encode())
               for given in proofs for expected in accepted)

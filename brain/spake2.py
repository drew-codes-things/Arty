import base64
import hashlib
import hmac
import re
import secrets

PRIME = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AACAA68FFFFFFFFFFFFFFFF", 16)
GENERATOR = 2
ORDER = (PRIME - 1) // 2
ELEMENT_BYTES = 256
SHARE_HEX_CHARS = ELEMENT_BYTES * 2
SHARE_FORMAT = re.compile(f"[0-9a-f]{{{SHARE_HEX_CHARS}}}")
CONTEXT = b"arty-phone-link-v1"
HASH_BLOCKS_PER_ELEMENT = 5
APP = "app"
SEALING_APP = "app-sealed-1"
ARTY = "arty"
WRONG_SHAPE = "A linking share is 512 lowercase hex characters."
SEAL_NONCE_BYTES = 16
SEAL_TAG_BYTES = 32
SEAL_BLOCK_BYTES = 32
SEAL_COUNTER_BYTES = 4
SEALED_FORMAT = re.compile("(?:[0-9a-f]{2})+")


class BadShare(ValueError):
    pass


class BadSeal(ValueError):
    pass


def _hashed_element(label):
    blocks = b"".join(hashlib.sha512(CONTEXT + b"|" + label + bytes([index])).digest()
                      for index in range(HASH_BLOCKS_PER_ELEMENT))
    return pow(int.from_bytes(blocks, "big") % PRIME, 2, PRIME)


BLINDS = {APP: _hashed_element(b"M"), ARTY: _hashed_element(b"N")}


def _other(role):
    return ARTY if role == APP else APP


def _code_scalar(code):
    return int.from_bytes(hashlib.sha512(CONTEXT + b"|code|" + code.encode("utf-8")).digest(), "big") % ORDER


def _element_bytes(element):
    return element.to_bytes(ELEMENT_BYTES, "big")


def new_secret():
    return secrets.randbelow(ORDER - 1) + 1


def share_hex(secret, code, role):
    element = pow(GENERATOR, secret, PRIME) * pow(BLINDS[role], _code_scalar(code), PRIME) % PRIME
    return _element_bytes(element).hex()


def has_share_format(text):
    return isinstance(text, str) and bool(SHARE_FORMAT.fullmatch(text))


def element_from_hex(text):
    if not has_share_format(text):
        raise BadShare(WRONG_SHAPE)
    element = int(text, 16)
    is_in_group = 1 < element < PRIME - 1 and pow(element, ORDER, PRIME) == 1
    if not is_in_group:
        raise BadShare("That linking share is not a value this link can use.")
    return element


def shared_key(secret, code, role, app_share, arty_share):
    their_share = element_from_hex(arty_share if role == APP else app_share)
    element_from_hex(app_share if role == APP else arty_share)
    scalar = _code_scalar(code)
    unblinded = their_share * pow(BLINDS[_other(role)], ORDER - scalar, PRIME) % PRIME
    shared = pow(unblinded, secret, PRIME)
    transcript = (CONTEXT + bytes.fromhex(app_share) + bytes.fromhex(arty_share) + _element_bytes(shared)
                  + _element_bytes(scalar))
    return hashlib.sha256(transcript).digest()


def proof(key, role):
    return hmac.new(key, CONTEXT + b"|proof|" + role.encode("ascii"), hashlib.sha256).hexdigest()


def link_token(key):
    digest = hmac.new(key, CONTEXT + b"|token", hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _keyed(key, label):
    return hmac.new(key, CONTEXT + label, hashlib.sha256).digest()


def _keystream(key, nonce, length):
    blocks = -(-length // SEAL_BLOCK_BYTES)
    stream_key = _keyed(key, b"|seal-stream")
    return b"".join(hmac.new(stream_key, nonce + counter.to_bytes(SEAL_COUNTER_BYTES, "big"), hashlib.sha256).digest()
                    for counter in range(blocks))[:length]


def _seal_tag(key, nonce, body):
    return hmac.new(_keyed(key, b"|seal-tag"), nonce + body, hashlib.sha256).digest()


def seal(key, plain, nonce=None):
    nonce = nonce or secrets.token_bytes(SEAL_NONCE_BYTES)
    body = bytes(a ^ b for a, b in zip(plain, _keystream(key, nonce, len(plain))))
    return (nonce + body + _seal_tag(key, nonce, body)).hex()


def open_sealed(key, sealed):
    if not isinstance(sealed, str) or not SEALED_FORMAT.fullmatch(sealed):
        raise BadSeal("The sealed answer isn't hex.")
    raw = bytes.fromhex(sealed)
    if len(raw) < SEAL_NONCE_BYTES + SEAL_TAG_BYTES:
        raise BadSeal("The sealed answer is too short.")
    nonce, body, tag = raw[:SEAL_NONCE_BYTES], raw[SEAL_NONCE_BYTES:-SEAL_TAG_BYTES], raw[-SEAL_TAG_BYTES:]
    if not hmac.compare_digest(tag, _seal_tag(key, nonce, body)):
        raise BadSeal("The sealed answer was changed on the way or made with another key.")
    return bytes(a ^ b for a, b in zip(body, _keystream(key, nonce, len(body))))

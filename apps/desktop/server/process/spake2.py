import base64
import hashlib
import hmac
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
CONTEXT = b"arty-link-pair-v1"
HASH_BLOCKS_PER_ELEMENT = 5
APP = "app"
ARTY = "arty"


class BadShare(ValueError):
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


def element_from_hex(text):
    if not isinstance(text, str) or len(text) != SHARE_HEX_CHARS:
        raise BadShare("A pairing share is 512 hex characters.")
    try:
        element = int(text, 16)
    except ValueError:
        raise BadShare("A pairing share is 512 hex characters.")
    is_in_group = 1 < element < PRIME - 1 and pow(element, ORDER, PRIME) == 1
    if not is_in_group:
        raise BadShare("That pairing share is not a value this pairing can use.")
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

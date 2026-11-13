"""Only Arty's own callers may use the local service: it can control the computer."""
import base64
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import arty_server

client = TestClient(arty_server.app, base_url="http://127.0.0.1:8765")


def status(**headers):
    return client.get("/computer", headers=headers).status_code


def test_the_arty_window_and_local_scripts_get_in_with_the_token():
    assert status(**{"X-Arty-Token": arty_server.TOKEN}) == 200


ARTY_EXTENSION = f"chrome-extension://{arty_server.EXTENSION_ID}"
MANIFEST = Path(__file__).resolve().parents[4] / "extensions" / "browser" / "manifest.json"


def extension_id_from(manifest_path):
    key = base64.b64decode(json.loads(manifest_path.read_text())["key"])
    return "".join(chr(ord("a") + int(digit, 16)) for digit in hashlib.sha256(key).hexdigest()[:32])


def test_the_pinned_id_is_the_one_chrome_gives_the_arty_extension():
    assert arty_server.EXTENSION_ID == extension_id_from(MANIFEST)


def test_the_arty_extension_reaches_only_the_browser_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(arty_server, "PAIRING_FILE", tmp_path / ".arty_browser_pairing")
    monkeypatch.setattr(arty_server, "_pairing", {})
    paired = {"Origin": ARTY_EXTENSION, "X-Arty-Pairing": arty_server.pairing_code()}
    assert client.post("/browser/next?timeout=0", headers=paired).status_code == 200
    result = client.post("/browser/result", headers=paired, json={"id": 0, "result": {}})
    assert result.status_code == 200
    assert status(Origin=ARTY_EXTENSION) == 403
    assert client.post("/confirm", headers={"Origin": ARTY_EXTENSION}, json={"id": "x", "ok": True}).status_code == 403
    assert client.post("/computer", headers={"Origin": ARTY_EXTENSION}, json={"enabled": True}).status_code == 403
    assert client.post("/task/1/approve", headers={"Origin": ARTY_EXTENSION}).status_code == 403
    assert client.get("/history", headers={"Origin": ARTY_EXTENSION}).status_code == 403
    assert client.post("/chat/stream", headers={"Origin": ARTY_EXTENSION}, json={"text": "hi"}).status_code == 403


def test_other_extensions_and_forged_fetch_metadata_are_refused():
    other = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
    assert status(**{"Sec-Fetch-Site": "none"}) == 403
    assert status(Origin=other) == 403
    assert client.post("/browser/next?timeout=0", headers={"Origin": other}).status_code == 403
    assert client.post("/browser/result", headers={"Origin": other}, json={"id": 0, "result": {}}).status_code == 403
    assert client.post("/browser/next?timeout=0", headers={"Sec-Fetch-Site": "none"}).status_code == 403


def test_web_pages_are_refused_however_they_try():
    assert status() == 403                                           # no token at all
    assert status(**{"X-Arty-Token": "guess"}) == 403
    assert status(Origin="null", **{"Sec-Fetch-Site": "cross-site"}) == 403   # sandboxed frame
    assert status(**{"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"}) == 403  # <img> tag
    assert status(Origin="https://evil.example") == 403


def test_dns_rebinding_is_refused_even_with_the_token():
    response = client.get("/computer", headers={"Host": "evil.example:8765", "X-Arty-Token": arty_server.TOKEN})
    assert response.status_code == 403


def test_a_token_with_characters_outside_ascii_is_refused_not_a_crash():
    assert status(**{"X-Arty-Token": "caf\u00e9".encode()}) == 403


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no Unix permission bits")
def test_an_old_token_file_readable_by_others_is_tightened(tmp_path, monkeypatch):
    token_file = tmp_path / ".arty_token"
    token_file.write_text("old")
    token_file.chmod(0o644)
    monkeypatch.setattr(arty_server, "TOKEN_FILE", token_file)
    arty_server._write_token()
    assert token_file.stat().st_mode & 0o777 == 0o600
    assert token_file.read_text() == arty_server.TOKEN


def test_the_service_proves_it_holds_this_launchs_token_without_being_sent_it(monkeypatch):
    monkeypatch.setattr(arty_server, "TOKEN", "token-for-the-test")
    response = client.get("/whoami", params={"nonce": "00112233445566778899aabbccddeeff"})
    assert response.status_code == 200
    assert response.json() == {"proof": "1595e14e468ad0570764971152d859f22ebc14291d012bf85a719d4d612222fc"}
    assert client.get("/whoami", params={"nonce": "not-hex"}).status_code == 400
    assert client.get("/whoami", headers={"Host": "evil.example:8765"}, params={"nonce": "0" * 32}).status_code == 403


FIREFOX_EXTENSION = "moz-extension://4f0a8c1e-2b7d-4c55-9e1a-0d3b6f7a9c21"


def test_the_arty_extension_in_firefox_reaches_only_the_browser_queue_with_the_pairing_code(tmp_path, monkeypatch):
    monkeypatch.setattr(arty_server, "PAIRING_FILE", tmp_path / ".arty_browser_pairing")
    monkeypatch.setattr(arty_server, "_pairing", {})
    paired = {"Origin": FIREFOX_EXTENSION, "X-Arty-Pairing": arty_server.pairing_code()}
    assert client.post("/browser/next?timeout=0", headers=paired).status_code == 200
    assert client.post("/browser/result", headers=paired, json={"id": 0, "result": {}}).status_code == 200
    assert client.post("/browser/next?timeout=0", headers={"Origin": FIREFOX_EXTENSION, "X-Arty-Pairing": "0" * 32}).status_code == 403
    assert status(**paired) == 403
    assert client.post("/chat/stream", headers=paired, json={"text": "hi"}).status_code == 403
    for forged in ("moz-extension://not-a-uuid", "moz-extension://4f0a8c1e-2b7d-4c55-9e1a-0d3b6f7a9c21.evil.example",
                   "https://moz-extension.example"):
        assert client.post("/browser/next?timeout=0", headers={**paired, "Origin": forged}).status_code == 403, forged


def test_the_firefox_extension_passes_the_cors_check():
    preflight = client.options("/browser/next", headers={"Origin": FIREFOX_EXTENSION, "Access-Control-Request-Method": "POST",
                                                         "Access-Control-Request-Headers": "X-Arty-Pairing"})
    assert preflight.headers.get("access-control-allow-origin") == FIREFOX_EXTENSION
    other = client.options("/browser/next", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert other.headers.get("access-control-allow-origin") is None

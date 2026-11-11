import hashlib
import hmac
import json
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import devices  # noqa: E402
from test_review_brain import KEY, Server  # noqa: E402

import brain  # noqa: E402
import store  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Requests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.requests = devices.Requests(clock=self.clock)

    def test_a_code_is_short_readable_and_unique(self):
        made = [self.requests.start("Arty CLI on a", "192.0.2.1") for _ in range(3)]
        codes = {request.user_code for request in made}
        self.assertEqual(len(codes), 3)
        for code in codes:
            self.assertRegex(code, r"^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$")
        self.assertTrue(all(len(request.device_code) >= 40 for request in made))

    def test_approval_hands_the_key_over_once(self):
        request = self.requests.start("Arty CLI on a", "192.0.2.1")
        self.assertEqual(self.requests.collect(request.device_code, "app-key"), (202, {"status": "pending"}))
        self.assertTrue(self.requests.answer(request.user_code.lower().replace("-", ""), approve=True))
        self.assertEqual(self.requests.collect(request.device_code, "app-key"), (200, {"api_key": "app-key"}))
        self.assertEqual(self.requests.collect(request.device_code, "app-key")[0], 410)

    def test_a_denied_request_is_told_and_forgotten(self):
        request = self.requests.start("x", "192.0.2.1")
        self.requests.answer(request.user_code, approve=False)
        self.assertEqual(self.requests.collect(request.device_code, "k")[0], 403)
        self.assertEqual(self.requests.collect(request.device_code, "k")[0], 410)

    def test_codes_run_out(self):
        request = self.requests.start("x", "192.0.2.1")
        self.clock.now += devices.LIFETIME_SECONDS + 1
        self.assertIsNone(self.requests.pending(request.user_code))
        self.assertFalse(self.requests.answer(request.user_code, approve=True))
        self.assertEqual(self.requests.collect(request.device_code, "k")[0], 410)

    def test_one_address_cannot_fill_the_list(self):
        for _ in range(devices.MAX_PER_ADDRESS):
            self.requests.start("x", "192.0.2.1")
        with self.assertRaises(devices.DeviceError) as raised:
            self.requests.start("x", "192.0.2.1")
        self.assertEqual(raised.exception.status, 429)
        self.requests.start("x", "192.0.2.2")

    def test_the_name_is_cleaned_and_shortened(self):
        request = self.requests.start("Arty\nCLI\x00 " + "x" * 200, "192.0.2.1")
        self.assertEqual(len(request.name), devices.MAX_NAME_CHARS)
        self.assertNotIn("\n", request.name)
        self.assertEqual(self.requests.start("", "192.0.2.1").name, devices.UNNAMED)

    def test_an_answered_code_is_no_longer_pending(self):
        request = self.requests.start("x", "192.0.2.1")
        self.requests.answer(request.user_code, approve=True)
        self.assertIsNone(self.requests.pending(request.user_code))
        self.assertFalse(self.requests.answer(request.user_code, approve=False))


class DeviceApi(Server):
    def test_the_whole_flow_over_http(self):
        status, body = self.request("POST", "/v1/device/start", body={"name": "Arty CLI on test"}, authorised=False)
        self.assertEqual(status, 200)
        started = json.loads(body)
        self.assertEqual(started["verify_path"], f"/ios/?authorise={started['user_code']}")
        self.assertGreater(started["expires_in"], 0)
        status, _ = self.request("POST", "/v1/device/token", body={"device_code": started["device_code"]}, authorised=False)
        self.assertEqual(status, 202)
        status, body = self.request("GET", f"/v1/device/pending?code={started['user_code']}")
        self.assertEqual((status, json.loads(body)["name"]), (200, "Arty CLI on test"))
        status, _ = self.request("POST", "/v1/device/approve", body={"user_code": started["user_code"], "approve": True})
        self.assertEqual(status, 200)
        status, body = self.request("POST", "/v1/device/token", body={"device_code": started["device_code"]}, authorised=False)
        self.assertEqual(status, 200)
        device_key = json.loads(body)["api_key"]
        self.assertNotEqual(device_key, KEY)
        status, _ = self.request("GET", "/v1/tasks", headers={"Authorization": f"Bearer {device_key}"}, authorised=False)
        self.assertEqual(status, 200)

    def test_looking_up_and_approving_need_the_key(self):
        status, _ = self.request("GET", "/v1/device/pending?code=ABCD-EFGH", authorised=False)
        self.assertEqual(status, 401)
        status, _ = self.request("POST", "/v1/device/approve", body={"user_code": "ABCD-EFGH", "approve": True}, authorised=False)
        self.assertEqual(status, 401)

    def test_unknown_codes_and_bad_bodies(self):
        self.assertEqual(self.request("GET", "/v1/device/pending?code=ABCD-EFGH")[0], 404)
        self.assertEqual(self.request("POST", "/v1/device/approve", body={"user_code": "ABCD-EFGH", "approve": True})[0], 404)
        self.assertEqual(self.request("POST", "/v1/device/token", body={"device_code": 5}, authorised=False)[0], 400)
        self.assertEqual(self.request("POST", "/v1/device/token", body={"device_code": "nope"}, authorised=False)[0], 410)
        self.assertEqual(self.request("POST", "/v1/device/start", body={"name": ["x"]}, authorised=False)[0], 400)


class DeviceSafety(Server):
    def setUp(self):
        super().setUp()
        patcher = unittest.mock.patch.object(brain, "device_requests", devices.Requests())
        patcher.start()
        self.addCleanup(patcher.stop)
        with store.write_lock, store.connect() as connection:
            connection.execute("DELETE FROM approved_devices")

    def start(self, headers=None, name="Arty CLI on test"):
        return self.request("POST", "/v1/device/start", body={"name": name}, headers=headers, authorised=False)

    def approved_key(self, name="Arty CLI on test"):
        started = json.loads(self.start(name=name)[1])
        self.request("POST", "/v1/device/approve", body={"user_code": started["user_code"], "approve": True})
        return json.loads(self.request("POST", "/v1/device/token", body={"device_code": started["device_code"]},
                                       authorised=False)[1])["api_key"]

    def test_a_forwarded_address_from_an_untrusted_peer_is_ignored(self):
        started = json.loads(self.start(headers={"Cf-Connecting-Ip": "203.0.113.9"})[1])
        status, body = self.request("GET", f"/v1/device/pending?code={started['user_code']}")
        self.assertEqual((status, json.loads(body)["address"]), (200, "127.0.0.1"))

    def test_spoofed_addresses_cannot_get_round_the_per_address_limit(self):
        for number in range(devices.MAX_PER_ADDRESS):
            self.assertEqual(self.start(headers={"Cf-Connecting-Ip": f"203.0.113.{number}"})[0], 200)
        self.assertEqual(self.start(headers={"Cf-Connecting-Ip": "203.0.113.99"})[0], 429)

    def test_a_trusted_proxy_passes_on_the_real_address(self):
        with unittest.mock.patch.object(brain, "TRUSTED_PROXIES", frozenset({"127.0.0.1"})):
            started = json.loads(self.start(headers={"Cf-Connecting-Ip": "203.0.113.9"})[1])
            body = self.request("GET", f"/v1/device/pending?code={started['user_code']}")[1]
        self.assertEqual(json.loads(body)["address"], "203.0.113.9")

    def test_approve_must_be_a_real_yes(self):
        started = json.loads(self.start()[1])
        for value in ("false", "no", 1, None):
            status, _ = self.request("POST", "/v1/device/approve", body={"user_code": started["user_code"], "approve": value})
            self.assertEqual(status, 400, value)
        status, _ = self.request("GET", f"/v1/device/pending?code={started['user_code']}")
        self.assertEqual(status, 200)

    def test_each_device_gets_its_own_key_that_can_be_listed_and_revoked(self):
        first, second = self.approved_key("laptop"), self.approved_key("desktop")
        self.assertNotEqual(first, second)
        status, body = self.request("GET", "/v1/devices")
        listed = json.loads(body)["devices"]
        self.assertEqual(status, 200)
        self.assertEqual(sorted(device["name"] for device in listed), ["desktop", "laptop"])
        self.assertNotIn(first, body.decode())
        self.assertNotIn(hashlib.sha256(first.encode()).hexdigest(), body.decode())
        laptop = next(device for device in listed if device["name"] == "laptop")
        self.assertEqual(self.request("DELETE", f"/v1/devices/{laptop['id']}")[0], 200)
        bearer = {"Authorization": f"Bearer {first}"}
        self.assertEqual(self.request("GET", "/v1/tasks", headers=bearer, authorised=False)[0], 401)
        self.assertEqual(self.request("GET", "/v1/tasks", headers={"Authorization": f"Bearer {second}"}, authorised=False)[0], 200)
        self.assertEqual(self.request("GET", "/v1/tasks")[0], 200)
        self.assertEqual(self.request("DELETE", f"/v1/devices/{laptop['id']}")[0], 404)

    def test_a_device_key_can_check_the_brain_is_really_its_own(self):
        device_key = self.approved_key()
        nonce = "00112233445566778899aabb"
        body = self.request("GET", f"/v1/identity?nonce={nonce}", authorised=False)[1]
        expected = hmac.new(hashlib.sha256(device_key.encode()).hexdigest().encode(), f"arty-identity:{nonce}".encode(),
                            hashlib.sha256).hexdigest()
        self.assertIn(expected, json.loads(body)["proofs"])


class StartLimit(unittest.TestCase):
    def test_many_addresses_together_are_still_limited(self):
        clock = Clock()
        requests = devices.Requests(clock=clock)
        for number in range(devices.MAX_STARTS_PER_MINUTE):
            requests.start("x", f"198.51.100.{number}")
            requests.by_device.clear()
        with self.assertRaises(devices.DeviceError) as raised:
            requests.start("x", "198.51.100.250")
        self.assertEqual(raised.exception.status, 429)
        clock.now += 61
        requests.start("x", "198.51.100.251")


if __name__ == "__main__":
    unittest.main()

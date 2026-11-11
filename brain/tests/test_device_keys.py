import hashlib
import json
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import devices  # noqa: E402
from test_review_brain import Server  # noqa: E402

import brain  # noqa: E402
import store  # noqa: E402

STARFIRE = "https://starfire.example"


class ApprovePage(Server):
    def approver(self):
        status, body = self.request("GET", "/v1/device/approver", authorised=False)
        return status, json.loads(body)

    def test_with_no_starfire_set_there_is_no_approve_page(self):
        with unittest.mock.patch.object(brain, "STARFIRE_URL", ""):
            self.assertEqual(self.approver(), (200, {"approve_page": ""}))

    def test_the_approve_page_is_starfires_devices_page_and_needs_no_key(self):
        with unittest.mock.patch.object(brain, "STARFIRE_URL", STARFIRE + "/"):
            self.assertEqual(self.approver(), (200, {"approve_page": f"{STARFIRE}/arty-devices.html"}))

    def test_only_web_addresses_become_an_approve_page(self):
        for setting in ("javascript:alert(1)", "ftp://starfire.example", "https://", "starfire.example", " "):
            self.assertEqual(devices.approve_page(setting), "", setting)
        self.assertEqual(devices.approve_page("http://192.0.2.16:8090/base/"), "http://192.0.2.16:8090/base/arty-devices.html")


class MadeKeys(Server):
    def setUp(self):
        super().setUp()
        with store.write_lock, store.connect() as connection:
            connection.execute("DELETE FROM approved_devices")

    def make(self, name="Sam's phone", headers=None, authorised=True):
        status, body = self.request("POST", "/v1/devices", body={"name": name}, headers=headers, authorised=authorised)
        return status, json.loads(body)

    def test_the_main_key_makes_a_named_device_key_that_is_shown_once(self):
        status, made = self.make()
        self.assertEqual(status, 201)
        self.assertEqual(made["name"], "Sam's phone")
        self.assertTrue(made["api_key"].startswith(devices.DEVICE_KEY_PREFIX))
        bearer = {"Authorization": f"Bearer {made['api_key']}"}
        self.assertEqual(self.request("GET", "/v1/tasks", headers=bearer, authorised=False)[0], 200)
        listed = self.request("GET", "/v1/devices")[1].decode()
        self.assertIn("Sam's phone", listed)
        self.assertNotIn(made["api_key"], listed)
        self.assertNotIn(hashlib.sha256(made["api_key"].encode()).hexdigest(), listed)

    def test_the_app_key_can_make_keys_too(self):
        with unittest.mock.patch.object(brain, "APP_KEY", "app-key-for-test"):
            status, _ = self.make(headers={"Authorization": "Bearer app-key-for-test"}, authorised=False)
        self.assertEqual(status, 201)

    def test_a_device_key_cannot_make_more_keys(self):
        device_key = self.make()[1]["api_key"]
        status, answer = self.make(headers={"Authorization": f"Bearer {device_key}"}, authorised=False)
        self.assertEqual(status, 403)
        self.assertNotIn("api_key", answer)
        self.assertEqual(len(store.list_devices()), 1)

    def test_making_a_key_needs_a_key(self):
        self.assertEqual(self.make(authorised=False)[0], 401)
        self.assertEqual(store.list_devices(), [])

    def test_the_name_is_cleaned_and_must_be_text(self):
        status, made = self.make("phone\n\x00" + "x" * 200)
        self.assertEqual(status, 201)
        self.assertEqual(len(made["name"]), devices.MAX_NAME_CHARS)
        self.assertNotIn("\n", made["name"])
        self.assertEqual(self.make(["phone"])[0], 400)
        self.assertEqual(self.make("")[1]["name"], devices.UNNAMED)

    def test_there_is_a_limit_on_how_many_keys_are_live(self):
        with unittest.mock.patch.object(devices, "MAX_ACTIVE_DEVICES", 2):
            self.assertEqual(self.make("one")[0], 201)
            self.assertEqual(self.make("two")[0], 201)
            self.assertEqual(self.make("three")[0], 429)
        self.assertEqual(len(store.list_devices()), 2)


if __name__ == "__main__":
    unittest.main()

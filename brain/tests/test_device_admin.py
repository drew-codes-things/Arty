import json
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import devices  # noqa: E402
from test_review_brain import Server  # noqa: E402

import brain  # noqa: E402
import api_keys  # noqa: E402
import store  # noqa: E402

APP_KEY = "app-key-for-test"


class DeviceAdminNeedsTheMainKey(Server):
    def setUp(self):
        super().setUp()
        patcher = unittest.mock.patch.object(brain, "device_requests", devices.Requests())
        patcher.start()
        self.addCleanup(patcher.stop)
        with store.write_lock, store.connect() as connection:
            connection.execute("DELETE FROM approved_devices")

    def started(self, name="Arty CLI on test"):
        return json.loads(self.request("POST", "/v1/device/start", body={"name": name}, authorised=False)[1])

    def approve(self, user_code, headers=None, authorised=True):
        return self.request("POST", "/v1/device/approve", body={"user_code": user_code, "approve": True},
                            headers=headers, authorised=authorised)[0]

    def device_key(self, name):
        started = self.started(name)
        self.approve(started["user_code"])
        return json.loads(self.request("POST", "/v1/device/token", body={"device_code": started["device_code"]},
                                       authorised=False)[1])["api_key"]

    def as_device(self, key):
        return {"Authorization": f"Bearer {key}"}

    def device_id(self, name):
        listed = json.loads(self.request("GET", "/v1/devices")[1])["devices"]
        return next(device["id"] for device in listed if device["name"] == name)

    def test_a_device_key_cannot_approve_a_new_device(self):
        holder = self.device_key("laptop")
        waiting = self.started("stranger")
        self.assertEqual(self.approve(waiting["user_code"], self.as_device(holder), authorised=False), 403)
        status, _ = self.request("POST", "/v1/device/token", body={"device_code": waiting["device_code"]}, authorised=False)
        self.assertNotEqual(status, 200)
        self.assertEqual(self.request("GET", f"/v1/device/pending?code={waiting['user_code']}")[0], 200)

    def test_a_device_key_cannot_revoke_a_device(self):
        holder, other = self.device_key("laptop"), self.device_key("desktop")
        status, _ = self.request("DELETE", f"/v1/devices/{self.device_id('desktop')}", headers=self.as_device(holder),
                                 authorised=False)
        self.assertEqual(status, 403)
        self.assertEqual(self.request("GET", "/v1/tasks", headers=self.as_device(other), authorised=False)[0], 200)

    def test_a_device_key_cannot_revoke_itself_either(self):
        holder = self.device_key("laptop")
        status, _ = self.request("DELETE", f"/v1/devices/{self.device_id('laptop')}", headers=self.as_device(holder),
                                 authorised=False)
        self.assertEqual(status, 403)

    def test_the_main_key_still_approves_and_revokes(self):
        waiting = self.started("phone")
        self.assertEqual(self.approve(waiting["user_code"]), 200)
        self.device_key("laptop")
        self.assertEqual(self.request("DELETE", f"/v1/devices/{self.device_id('laptop')}")[0], 200)

    def test_the_app_key_approves_and_revokes_too(self):
        app = {"Authorization": f"Bearer {APP_KEY}"}
        with unittest.mock.patch.object(api_keys, "APP_KEY", APP_KEY):
            self.assertEqual(self.approve(self.started("phone")["user_code"], app, authorised=False), 200)
            self.device_key("laptop")
            status, _ = self.request("DELETE", f"/v1/devices/{self.device_id('laptop')}", headers=app, authorised=False)
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import devices  # noqa: E402
from test_review_brain import Server  # noqa: E402

import brain  # noqa: E402


class UnkeyedBodiesAreSmall(Server):
    def unkeyed(self, step, size):
        padding = "x" * size
        return self.request("POST", f"/v1/device/{step}", body={"name": "laptop", "device_code": "abc", "padding": padding},
                            authorised=False)[0]

    def test_an_unkeyed_device_start_refuses_a_large_body(self):
        self.assertEqual(self.unkeyed("start", brain.UNKEYED_BODY_BYTES), 413)

    def test_an_unkeyed_token_request_refuses_a_large_body(self):
        self.assertEqual(self.unkeyed("token", brain.UNKEYED_BODY_BYTES), 413)

    def test_a_normal_unkeyed_request_still_fits(self):
        with unittest.mock.patch.object(brain, "device_requests", devices.Requests()):
            self.assertEqual(self.request("POST", "/v1/device/start", body={"name": "laptop"}, authorised=False)[0], 200)


if __name__ == "__main__":
    unittest.main()

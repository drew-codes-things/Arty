import http.client
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_review_brain import Server  # noqa: E402

import brain  # noqa: E402

DROPPED_CONNECTIONS = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)
NOT_HANG_UPS = (KeyError("broken"), TimeoutError("the model took too long"), ConnectionRefusedError("model server down"))
ROUTES = (("GET", "/v1/tasks", "dispatch_get"), ("POST", "/v1/tasks", "dispatch_post"),
          ("DELETE", "/v1/tasks/1", "dispatch_delete"))


class AppsThatHangUp(Server):
    def ask_while_the_app_drops(self, method, path, dispatcher, error):
        with unittest.mock.patch.object(brain.Handler, dispatcher, side_effect=error), \
                unittest.mock.patch.object(brain.Handler, "send_json") as sent, \
                self.assertLogs("brain", "INFO") as logged:
            try:
                self.request(method, path, body={} if method == "POST" else None)
            except http.client.HTTPException:
                pass
        return sent, logged.output

    def test_every_dropped_connection_is_logged_as_a_hang_up_not_a_failure(self):
        for method, path, dispatcher in ROUTES:
            for error in DROPPED_CONNECTIONS:
                with self.subTest(method=method, error=error.__name__):
                    sent, logged = self.ask_while_the_app_drops(method, path, dispatcher, error)
                    self.assertTrue(any("hung up before the answer" in line for line in logged), logged)
                    self.assertFalse(any("request failed" in line for line in logged), logged)
                    sent.assert_not_called()

    def test_an_error_that_is_not_a_hang_up_still_answers_500(self):
        for method, path, dispatcher in ROUTES:
            for error in NOT_HANG_UPS:
                with self.subTest(method=method, error=type(error).__name__):
                    sent, logged = self.ask_while_the_app_drops(method, path, dispatcher, error)
                    sent.assert_called_once_with(500, {"error": {"message": brain.GENERIC_ERROR}})
                    self.assertTrue(any("request failed" in line for line in logged), logged)


if __name__ == "__main__":
    unittest.main()

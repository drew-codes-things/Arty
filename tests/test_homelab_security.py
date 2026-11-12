import importlib.machinery
import importlib.util
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "homelab-security"
NEW_DEVICE = {"aa:bb:cc:dd:ee:ff": {"ip": "192.168.1.77", "vendor": "Acme"}}


def load_watch():
    loader = importlib.machinery.SourceFileLoader("homelab_security", str(SCRIPT))
    spec = importlib.util.spec_from_loader("homelab_security", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class Brain:
    def __init__(self):
        self.reachable = False
        self.delivered = []

    def urlopen(self, request, timeout=None):
        if not self.reachable:
            raise OSError("brain restarting")
        self.delivered.append(json.loads(request.data)["title"])
        return mock.MagicMock()


class UnsentAlertsTest(unittest.TestCase):
    def test_an_alert_that_could_not_be_sent_is_sent_on_a_later_run(self):
        watch = load_watch()
        brain = Brain()
        with TemporaryDirectory() as folder:
            config = Path(folder) / "arty.env"
            config.write_text("BRAIN_URL=http://brain:8081\nALERT_TOKEN=t\nNETWORK=192.168.1.0/24\n")
            state = Path(folder) / "state.json"
            state.write_text(json.dumps({"devices": {}, "ports_scanned": 9e18, "ssh_checked": 9e18}))
            with mock.patch.object(watch, "CONFIG", config), mock.patch.object(watch, "STATE", state), \
                    mock.patch.object(watch, "run", return_value=""), \
                    mock.patch.object(watch, "scan_devices", return_value=NEW_DEVICE), \
                    mock.patch.object(watch.urllib.request, "urlopen", brain.urlopen):
                watch.main()
                self.assertEqual(brain.delivered, [])
                brain.reachable = True
                watch.main()
        self.assertEqual(brain.delivered, ["📶 New device on your network"])


class HungBrain:
    def __init__(self):
        self.started = self.now = 1000.0
        self.tries = 0

    def monotonic(self):
        return self.now

    def urlopen(self, request, timeout=None):
        self.tries += 1
        self.now += timeout
        raise TimeoutError("timed out")


class ResendBudgetTest(unittest.TestCase):
    RUN_EVERY_SECONDS = 600

    def test_resending_to_a_brain_that_never_answers_stops_in_time_and_keeps_every_alert(self):
        watch = load_watch()
        brain = HungBrain()
        waiting = [{"title": f"alert {number}", "message": "m", "urgent": False} for number in range(watch.MAX_UNSENT)]
        state = {"unsent": list(waiting)}
        settings = {"BRAIN_URL": "http://brain:8081", "ALERT_TOKEN": "t"}
        with mock.patch.object(watch.urllib.request, "urlopen", brain.urlopen), \
                mock.patch.object(watch.time, "monotonic", brain.monotonic), \
                mock.patch("sys.stderr"):
            watch.resend_unsent(settings, state)
        spent = brain.now - brain.started
        self.assertLess(spent, self.RUN_EVERY_SECONDS)
        self.assertLessEqual(spent, watch.RESEND_BUDGET_SECONDS)
        self.assertLess(brain.tries, len(waiting))
        self.assertEqual(state["unsent"], waiting)


if __name__ == "__main__":
    unittest.main()

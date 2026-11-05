import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from policy import approval_reason, remember_tool_result  # noqa: E402

RULES = {"always": ["homelab__restart_guest", "homelab__start_guest", "homelab__pufferpanel_power"]}
STATUS = "Host pve: online\nCT 103 core-games (192.168.1.12): running, CPU 3%\nVM 105 ai-desktop: stopped"


class ApprovalWording(unittest.TestCase):
    def setUp(self):
        self.context = {}
        remember_tool_result(self.context, "homelab__proxmox_status", STATUS)

    def test_guests_are_named(self):
        self.assertEqual(approval_reason("homelab__restart_guest", {"vmid": 103}, self.context, RULES),
                         "restart CT 103 (core-games)")
        self.assertEqual(approval_reason("homelab__start_guest", {"vmid": 105}, self.context, RULES),
                         "start VM 105 (ai-desktop)")

    def test_unknown_guest_still_reads_plainly(self):
        self.assertEqual(approval_reason("homelab__restart_guest", {"vmid": 999}, {}, RULES), "restart guest 999")

    def test_pufferpanel_actions_name_the_server(self):
        self.assertEqual(approval_reason("homelab__pufferpanel_power", {"server": "A+J", "action": "stop"}, {}, RULES),
                         "stop A+J")

    def test_no_raw_dictionaries(self):
        reason = approval_reason("homelab__restart_guest", {"vmid": 103}, {}, RULES)
        self.assertNotIn("{", reason)


if __name__ == "__main__":
    unittest.main()

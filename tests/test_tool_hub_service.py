import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "services" / "tool-hub.service"
BRAIN_UNIT = ROOT / "services" / "brain.service"
DEPLOY = ROOT / "deploy.sh"


class ToolHubService(unittest.TestCase):
    def test_the_unit_runs_the_hub_from_its_own_venv_with_the_shipped_config(self):
        unit = UNIT.read_text()
        self.assertIn("ExecStart=/opt/ai-hub/bin/python /srv/ai/hub/tool_hub.py --host 127.0.0.1 --port 8000 "
                      "--config /srv/ai/mcp/tool-hub.json", unit)
        self.assertIn("EnvironmentFile=/srv/ai/secrets/mcpo.env", unit)

    def test_the_key_never_goes_on_the_command_line(self):
        exec_line = next(line for line in UNIT.read_text().splitlines() if line.startswith("ExecStart="))
        self.assertNotIn("KEY", exec_line.upper())

    def test_mcpo_is_gone_from_the_repository(self):
        for gone in ("services/mcpo.service", "bin/mcpo-start", "mcp/mcpo.json"):
            self.assertFalse((ROOT / gone).exists(), gone)

    def test_the_brain_waits_for_the_tool_hub(self):
        brain_unit = BRAIN_UNIT.read_text()
        self.assertIn("tool-hub.service", brain_unit)
        self.assertNotIn("mcpo.service", brain_unit)

    def test_deploy_switches_from_mcpo_to_the_tool_hub_only_once_the_venv_exists(self):
        script = DEPLOY.read_text()
        venv_check = script.index("/opt/ai-hub/bin/python")
        self.assertLess(venv_check, script.index("install -m 644 deploy/brain/*"))
        self.assertIn("systemctl disable --now mcpo", script)
        self.assertIn("systemctl restart tool-hub brain", script)
        self.assertIn("systemctl is-active tool-hub brain", script)
        self.assertNotIn("restart mcpo", script)


if __name__ == "__main__":
    unittest.main()

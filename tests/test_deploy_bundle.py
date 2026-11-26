import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAKE_SSH = """#!/bin/bash
printf '%s\\n' "$*" >> "$LOG_DIR/ssh.log"
case "$*" in
  *"python3 -I -"*) echo "${COOKIE_ROWS-0}"; cat > /dev/null; exit "${COOKIE_EXIT:-0}" ;;
  *"mktemp -d"*) echo /tmp/tmp.Fake5678 ;;
  *mktemp*) echo /tmp/tmp.Fake1234.tgz ;;
esac
cat > /dev/null
exit 0
"""
FAKE_SCP = """#!/bin/bash
count=$(ls "$LOG_DIR" | grep -c '^copy' || true)
for argument in "$@"; do
  case "$argument" in -*) ;; *) sources+=("$argument") ;; esac
done
destination=${sources[-1]}
printf '%s\\n' "$destination" >> "$LOG_DIR/scp.log"
cp "${sources[0]}" "$LOG_DIR/copy$count"
exit 0
"""


@unittest.skipUnless((ROOT / "desktop_known_hosts").is_file(), "needs the pinned desktop key file deploy.sh checks for")
class DeployBundle(unittest.TestCase):
    def setUp(self):
        self.logs = Path(tempfile.mkdtemp(prefix="arty-deploy-test-"))
        fake_bin = self.logs / "bin"
        fake_bin.mkdir()
        for name, body in (("ssh", FAKE_SSH), ("scp", FAKE_SCP)):
            (fake_bin / name).write_text(body)
            (fake_bin / name).chmod(0o755)
        env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "LOG_DIR": str(self.logs),
               "ARTY_HOSTS_FILE": "/dev/null", "DESKTOP_ADDRESS": "192.0.2.9", "ARTY_DISCORD_ID": "",
               "ARTY_TELEGRAM_ID": ""}
        self.run_result = subprocess.run([str(ROOT / "deploy.sh")], env=env, capture_output=True, text=True, timeout=120)

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.logs)], check=False)

    def bundle_members(self) -> list[str]:
        first_copy = self.logs / "copy0"
        with tarfile.open(first_copy) as bundle:
            return bundle.getnames()

    def test_the_host_only_egress_unit_is_not_shipped_to_the_container(self):
        self.assertEqual(self.run_result.returncode, 0, self.run_result.stderr)
        members = self.bundle_members()
        self.assertIn("services/brain.service", members)
        self.assertNotIn("services/arty-desktop-egress.service", members)
        self.assertFalse([name for name in members if name.startswith("mcp/test_")], members)

    def test_the_tool_hub_and_its_config_are_shipped_without_its_tests(self):
        self.assertEqual(self.run_result.returncode, 0, self.run_result.stderr)
        members = self.bundle_members()
        self.assertIn("hub/tool_hub.py", members)
        self.assertIn("hub/web_host.py", members)
        self.assertIn("mcp/tool-hub.json", members)
        self.assertIn("services/tool-hub.service", members)
        self.assertFalse([name for name in members if name.startswith("hub/tests") or "mcpo" in name], members)

    def test_every_brain_module_is_shipped(self):
        self.assertEqual(self.run_result.returncode, 0, self.run_result.stderr)
        members = set(self.bundle_members())
        modules = {f"brain/{path.name}" for path in (ROOT / "brain").glob("*.py")}
        self.assertIn("brain/agent_loop.py", modules)
        self.assertEqual(modules - members, set())

    def test_every_file_the_brain_reads_at_start_up_is_shipped(self):
        self.assertEqual(self.run_result.returncode, 0, self.run_result.stderr)
        members = set(self.bundle_members())
        for name in ("brain/brain.json", "brain/system-prompt.md", "brain/safety-prompt.md", "brain/persona.md"):
            with self.subTest(name=name):
                self.assertIn(name, members)

    def test_the_docs_library_and_its_units_are_shipped_and_started(self):
        self.assertEqual(self.run_result.returncode, 0, self.run_result.stderr)
        members = set(self.bundle_members())
        modules = {f"doclib/{path.name}" for path in (ROOT / "doclib").glob("*.py")}
        self.assertIn("doclib/server.py", modules)
        self.assertEqual(modules - members, set())
        self.assertTrue({"services/docs-library.service", "services/docs-library-refresh.service",
                         "services/docs-library-refresh.timer"} <= members)
        script = (ROOT / "deploy.sh").read_text()
        self.assertIn("install -m 644 deploy/doclib/*.py deploy/doclib/retrieval_set.json /srv/ai/doclib/", script)
        self.assertIn("systemctl enable docs-library docs-library-refresh.timer", script)
        self.assertIn("systemctl restart tool-hub brain docs-library", script)

    def test_the_bundle_goes_to_a_fresh_temporary_path_on_the_host(self):
        self.assertEqual(self.run_result.returncode, 0, self.run_result.stderr)
        first_destination = (self.logs / "scp.log").read_text().splitlines()[0]
        self.assertEqual(first_destination, "proxmox:/tmp/tmp.Fake1234.tgz")
        self.assertNotIn("/tmp/ai-deploy.tgz", (self.logs / "ssh.log").read_text())



@unittest.skipUnless((ROOT / "desktop_known_hosts").is_file(), "needs the pinned desktop key file deploy.sh checks for")
class ChatBots(unittest.TestCase):
    def deploy(self, **settings) -> tuple[subprocess.CompletedProcess, str]:
        with tempfile.TemporaryDirectory(prefix="arty-deploy-test-") as folder:
            logs = Path(folder)
            fake_bin = logs / "bin"
            fake_bin.mkdir()
            for name, body in (("ssh", FAKE_SSH), ("scp", FAKE_SCP)):
                (fake_bin / name).write_text(body)
                (fake_bin / name).chmod(0o755)
            env = {key: value for key, value in os.environ.items()
                   if key not in ("ARTY_DISCORD_ID", "ARTY_TELEGRAM_ID", "PUFFER_DIR")}
            env.update({"PATH": f"{fake_bin}:{os.environ['PATH']}", "LOG_DIR": str(logs),
                        "ARTY_HOSTS_FILE": "/dev/null", "DESKTOP_ADDRESS": "192.0.2.9", **settings})
            result = subprocess.run([str(ROOT / "deploy.sh")], env=env, capture_output=True, text=True, timeout=120)
            ssh_log = logs / "ssh.log"
            return result, ssh_log.read_text() if ssh_log.exists() else ""

    def test_no_bot_is_touched_unless_its_server_id_is_set(self):
        result, ssh_log = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("arty_discord.py skipped: no PufferPanel server id set", result.stdout)
        self.assertIn("arty_telegram.py skipped: no PufferPanel server id set", result.stdout)
        self.assertNotIn("docker", ssh_log)

    def test_a_bot_id_without_the_pufferpanel_folder_stops_before_anything_is_copied(self):
        result, ssh_log = self.deploy(ARTY_DISCORD_ID="abc123")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PUFFER_DIR", result.stderr)
        self.assertEqual(ssh_log, "")

    def test_a_pufferpanel_folder_with_shell_characters_is_refused(self):
        result, ssh_log = self.deploy(ARTY_DISCORD_ID="abc123", PUFFER_DIR="/srv/puffer'; reboot; '")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(ssh_log, "")

    def test_the_bot_is_copied_into_the_configured_pufferpanel_folder(self):
        result, ssh_log = self.deploy(ARTY_DISCORD_ID="abc123", PUFFER_DIR="/srv/puffer/servers")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("sha256sum '/srv/puffer/servers/abc123/arty_discord.py'", ssh_log)


STDIN_KEEPING_SSH = """#!/bin/bash
count=$(ls "$LOG_DIR" | grep -c '^stdin' || true)
printf '%s\\n' "$*" >> "$LOG_DIR/ssh.log"
printf '%s\\n' "$*" > "$LOG_DIR/command$count"
case "$*" in
  *"python3 -I -"*) echo 0 ;;
  *"mktemp -d"*) echo /tmp/tmp.Fake5678 ;;
  *mktemp*) echo /tmp/tmp.Fake1234.tgz ;;
esac
cat > "$LOG_DIR/stdin$count"
exit 0
"""
OWN_APPS = {"DRIVE_ADDRESS": "https://drive.example.org", "CALENDAR_ADDRESS": "https://calendar.example.org",
            "STARFIRE_ADDRESS": "https://starfire.example.org"}
OWN_APPS_WRITTEN = {"drive": "https://drive.example.org", "calendar": "https://calendar.example.org",
                    "starfire": "https://starfire.example.org"}


@unittest.skipUnless((ROOT / "desktop_known_hosts").is_file(), "needs the pinned desktop key file deploy.sh checks for")
class OwnAppAddresses(unittest.TestCase):
    def setUp(self):
        self.logs = Path(tempfile.mkdtemp(prefix="arty-deploy-test-"))
        fake_bin = self.logs / "bin"
        fake_bin.mkdir()
        for name, body in (("ssh", STDIN_KEEPING_SSH), ("scp", FAKE_SCP)):
            (fake_bin / name).write_text(body)
            (fake_bin / name).chmod(0o755)
        env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "LOG_DIR": str(self.logs),
               "ARTY_HOSTS_FILE": "/dev/null", "DESKTOP_ADDRESS": "192.0.2.9", "ARTY_DISCORD_ID": "",
               "ARTY_TELEGRAM_ID": "", **OWN_APPS}
        self.run_result = subprocess.run([str(ROOT / "deploy.sh")], env=env, capture_output=True, text=True, timeout=120)

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.logs)], check=False)

    def desktop_write(self) -> tuple[str, dict]:
        for command in sorted(self.logs.glob("command*")):
            if "own-apps.json" in command.read_text():
                stdin = self.logs / command.name.replace("command", "stdin")
                return command.read_text(), json.loads(stdin.read_text())
        self.fail("own-apps.json was never written on the desktop")

    def test_the_desktop_list_is_root_owned_outside_the_desktop_user_s_folders(self):
        self.assertEqual(self.run_result.returncode, 0, self.run_result.stderr)
        command, written = self.desktop_write()
        self.assertEqual(written, OWN_APPS_WRITTEN)
        self.assertIn("sudo install -d -o root -g root -m 755 /etc/arty", command)
        self.assertIn("sudo chown root:root /etc/arty/own-apps.json.new", command)
        self.assertIn("sudo mv /etc/arty/own-apps.json.new /etc/arty/own-apps.json", command)
        self.assertNotIn("cat > ~/.local/browser-agent/own-apps.json", command)

    def test_the_brain_gets_the_same_list_so_its_cards_treat_those_hosts_as_sensitive(self):
        self.assertEqual(self.run_result.returncode, 0, self.run_result.stderr)
        with tarfile.open(self.logs / "copy0") as bundle:
            listed = json.loads(bundle.extractfile("own-apps.json").read())
        self.assertEqual(listed, OWN_APPS_WRITTEN)
        self.assertIn("install -m 644 deploy/own-apps.json /srv/ai/brain/own-apps.json", (ROOT / "deploy.sh").read_text())

    def test_the_sign_in_launchers_are_installed_root_owned_outside_the_desktop_user_s_folders(self):
        self.assertEqual(self.run_result.returncode, 0, self.run_result.stderr)
        copied = (self.logs / "scp.log").read_text()
        self.assertNotIn(".local/bin/sign-in-", copied)
        self.assertNotIn(".local/share/applications/sign-in-", copied)
        self.assertIn("192.0.2.9:/tmp/tmp.Fake5678/", copied)
        installed = next(stdin.read_text() for stdin in sorted(self.logs.glob("stdin*"))
                         if "/usr/local/bin/$launcher" in stdin.read_text())
        self.assertIn('sudo install -o root -g root -m 755 "$upload/$launcher" /usr/local/bin/$launcher', installed)
        self.assertIn("sudo install -o root -g root -m 644 \"$upload/$launcher.desktop\" "
                      "/usr/local/share/applications/$launcher.desktop", installed)
        self.assertIn("rm -f ~/.local/bin/$launcher ~/.local/share/applications/$launcher.desktop", installed)
        script = (ROOT / "deploy.sh").read_text()
        self.assertIn("for launcher in sign-in-browser sign-in-google sign-in-apps", script)
        self.assertIn("sudo install -o root -g root -m 755 /usr/local/share/applications/$launcher.desktop "
                      "~/Desktop/$launcher.desktop", script)
        self.assertIn('if [ "$installed_sums" != "$(printf \'%s\' "$sums" | base64 -d)" ]; then', installed)
        self.assertIn("sudo rm -f /usr/local/bin/$launcher /usr/local/share/applications/$launcher.desktop", installed)
        self.assertIn("sha256sum < /usr/local/bin/doc2text", (self.logs / "ssh.log").read_text())
        for launcher in ("sign-in-browser", "sign-in-google", "sign-in-apps"):
            self.assertIn(f"Exec=/usr/local/bin/{launcher}\n", (ROOT / "arty-pc" / f"{launcher}.desktop").read_text())

    def test_the_desktop_agent_gets_the_shared_address_check(self):
        self.assertEqual(self.run_result.returncode, 0, self.run_result.stderr)
        self.assertIn("192.0.2.9:.local/browser-agent/web_host.py", (self.logs / "scp.log").read_text())

    def test_the_cookie_check_asks_about_every_own_app_host(self):
        self.assertEqual(self.run_result.returncode, 0, self.run_result.stderr)
        ssh_log = (self.logs / "ssh.log").read_text()
        self.assertIn("python3 -I - drive.example.org calendar.example.org starfire.example.org", ssh_log)


@unittest.skipUnless((ROOT / "desktop_known_hosts").is_file(), "needs the pinned desktop key file deploy.sh checks for")
class OwnAppCookieGate(unittest.TestCase):
    def deploy(self, **settings) -> tuple[subprocess.CompletedProcess, Path]:
        logs = Path(tempfile.mkdtemp(prefix="arty-deploy-test-"))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(logs)], check=False)
        fake_bin = logs / "bin"
        fake_bin.mkdir()
        for name, body in (("ssh", FAKE_SSH), ("scp", FAKE_SCP)):
            (fake_bin / name).write_text(body)
            (fake_bin / name).chmod(0o755)
        env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}", "LOG_DIR": str(logs),
               "ARTY_HOSTS_FILE": "/dev/null", "DESKTOP_ADDRESS": "192.0.2.9", "ARTY_DISCORD_ID": "",
               "ARTY_TELEGRAM_ID": "", **OWN_APPS, **settings}
        return subprocess.run([str(ROOT / "deploy.sh")], env=env, capture_output=True, text=True, timeout=120), logs

    def test_own_app_cookies_in_artys_browser_stop_the_deploy_before_anything_is_copied(self):
        result, logs = self.deploy(COOKIE_ROWS="3")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("3 cookie rows for an own app", result.stderr)
        self.assertFalse((logs / "scp.log").exists())
        self.assertEqual((logs / "ssh.log").read_text().count("\n"), 1)

    def test_a_count_that_fails_stops_the_deploy(self):
        result, logs = self.deploy(COOKIE_EXIT="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("couldn't count the own app cookies", result.stderr)
        self.assertFalse((logs / "scp.log").exists())

    def test_a_count_that_is_not_a_number_stops_the_deploy(self):
        result, logs = self.deploy(COOKIE_ROWS="")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((logs / "scp.log").exists())

    def test_a_clean_profile_lets_the_deploy_go_on(self):
        result, logs = self.deploy(COOKIE_ROWS="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((logs / "scp.log").exists())


if __name__ == "__main__":
    unittest.main()

import ast
import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE_URL = "http://127.0.0.1:11438"
ENGINE_SETTING = "ARTY_ENGINE_URL"
VISION_MODEL = "mimo-v2.6:9b"
DEPENDENCY_KEYS = ("Wants", "Requires", "After", "Before", "PartOf", "BindsTo", "Upholds", "Requisite", "OnFailure")
ORDERING_LINE = re.compile(rf"^[ \t]*({'|'.join(DEPENDENCY_KEYS)})=(.*)$", re.M)
KNOWN_UNITS = {"network-online.target", "network-pre.target", "tool-hub.service", "docker.service", "arty-engine.service",
               "systemd-modules-load.service", "systemd-udev-settle.service", "nvidia-devices.service", "pve-cluster.service",
               "pve-guests.service"}
OLD_BACKEND = "".join(("olla", "ma"))
MODEL_FAMILY = "".join(("lla", "ma"))
BANNED_WORDS = (OLD_BACKEND, MODEL_FAMILY + ".cpp", MODEL_FAMILY + "-server", MODEL_FAMILY + "_cpp", MODEL_FAMILY + "cpp",
                "".join(("gg", "ml")))
OLD_PORTS = re.compile(r"(?<![0-9])(?:%s)(?![0-9])" % "|".join(("".join(("114", "34")), "".join(("114", "36")))))
RECORDS = (".claude/history.md", "docs/release-notes-")
UNIT_FILES = ("services/*.service", "services/*.timer")


def banned_words_in(text: str) -> list[str]:
    lowered = text.lower()
    return [word for word in BANNED_WORDS if word in lowered]


def uses_old_ports(text: str) -> bool:
    return bool(OLD_PORTS.search(text))


def old_server_dependencies(text: str) -> list[str]:
    return [f"{key}={names}" for key, names in ORDERING_LINE.findall(text) if banned_words_in(names)]


def environment_default(path: Path, name: str) -> str:
    for node in ast.walk(ast.parse(path.read_text())):
        if (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "get" and len(node.args) == 2
                and isinstance(node.args[0], ast.Constant) and node.args[0].value == name):
            return node.args[1].value
    raise AssertionError(f"{path.name} reads no {name}")


def tracked_files() -> list[str]:
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [name for name in listed.split("\0") if name and not name.startswith(RECORDS) and (ROOT / name).is_file()]


def tracked_text_files() -> list[str]:
    readable = []
    for name in tracked_files():
        try:
            (ROOT / name).read_text()
        except UnicodeDecodeError:
            continue
        readable.append(name)
    return readable


def unit_files() -> list[Path]:
    return sorted(unit for pattern in UNIT_FILES for unit in ROOT.glob(pattern))


class UnitsWaitOnlyForArtysOwnParts(unittest.TestCase):
    def test_units_order_themselves_only_after_known_units(self):
        for unit in unit_files():
            for kind, names in ORDERING_LINE.findall(unit.read_text()):
                self.assertLessEqual(set(names.split()), KNOWN_UNITS, f"{unit.name} {kind}=")

    def test_no_unit_anywhere_depends_on_the_old_server(self):
        found = {name: old_server_dependencies((ROOT / name).read_text()) for name in tracked_text_files()}
        self.assertEqual({}, {name: lines for name, lines in found.items() if lines})

    def test_every_dependency_key_is_read(self):
        old_unit = f"{OLD_BACKEND}.service"
        probe = "\n".join(f"{key}=network-online.target {old_unit}" for key in DEPENDENCY_KEYS)
        self.assertEqual(len(DEPENDENCY_KEYS), len(old_server_dependencies(probe)))
        self.assertEqual([], old_server_dependencies("After=arty-engine.service\nPartOf=tool-hub.service"))
        self.assertIn("arty-engine.service", KNOWN_UNITS)

    def test_the_brain_still_wants_the_tool_hub(self):
        lines = dict(ORDERING_LINE.findall((ROOT / "services" / "brain.service").read_text()))
        self.assertIn("tool-hub.service", lines["Wants"].split())
        self.assertIn("tool-hub.service", lines["After"].split())


class ToolServersReachTheEngine(unittest.TestCase):
    def servers(self):
        return json.loads((ROOT / "mcp" / "tool-hub.json").read_text())["mcpServers"]

    def test_research_and_workspace_are_given_the_engine(self):
        servers = self.servers()
        self.assertEqual({ENGINE_SETTING: ENGINE_URL}, servers["research"]["env"])
        self.assertEqual({ENGINE_SETTING: ENGINE_URL, "VISION_MODEL": VISION_MODEL}, servers["workspace"]["env"])

    def test_the_defaults_are_the_engine_and_its_vision_model(self):
        self.assertEqual(ENGINE_URL, environment_default(ROOT / "mcp" / "research_server.py", ENGINE_SETTING))
        self.assertEqual(ENGINE_URL, environment_default(ROOT / "mcp" / "workspace_server.py", ENGINE_SETTING))
        self.assertEqual(VISION_MODEL, environment_default(ROOT / "mcp" / "workspace_server.py", "VISION_MODEL"))
        self.assertEqual(ENGINE_URL, environment_default(ROOT / "brain" / "settings.py", ENGINE_SETTING))


class ProvisioningUsesOnlyTheEngine(unittest.TestCase):
    def test_the_embedding_model_is_pulled_through_the_engine(self):
        text = (ROOT / "provision.sh").read_text()
        self.assertIn(f"engine=${{{ENGINE_SETTING}:-{ENGINE_URL}}}", text)
        self.assertIn('"$engine/api/pull"', text)


class NothingNamesTheOldBackend(unittest.TestCase):
    def test_no_tracked_file_outside_the_records_names_it_or_its_parts(self):
        named = {}
        for name in tracked_files():
            try:
                text = (ROOT / name).read_text()
            except UnicodeDecodeError:
                text = ""
            words = banned_words_in(text) + banned_words_in(name)
            if words:
                named[name] = sorted(set(words))
        self.assertEqual({}, named)

    def test_the_banned_words_cover_the_old_runtime_and_its_files(self):
        probe = " ".join((MODEL_FAMILY + ".cpp", f"({MODEL_FAMILY}-server)", "with", "gg" + "ml", "weights"))
        self.assertEqual(3, len(banned_words_in(probe)))
        self.assertEqual([], banned_words_in(json.dumps({"keywords": [MODEL_FAMILY, "qwen", "mistral"]})))

    def test_no_tracked_file_outside_the_records_uses_its_ports(self):
        used = [name for name in tracked_text_files() if uses_old_ports((ROOT / name).read_text())]
        self.assertEqual([], used)

    def test_a_port_only_counts_as_a_whole_number(self):
        port = "".join(("114", "34"))
        self.assertTrue(uses_old_ports(f"127.0.0.1:{port}/api"))
        self.assertFalse(uses_old_ports(f'{{"duration_ms": 2{port}}}'))
        self.assertFalse(uses_old_ports(f"{port}5 bytes"))


if __name__ == "__main__":
    unittest.main()

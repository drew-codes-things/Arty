import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import policy  # noqa: E402

RULES = json.loads((Path(__file__).resolve().parents[1] / "brain.json").read_text())["approval"]
INSTALLS = {
    "sudo": ["sudo systemctl restart nginx", "cd /tmp && sudo make install", "echo hi | sudo tee /etc/motd"],
    "apt": ["apt install -y nodejs npm", "sudo apt update && sudo apt install -y openjdk-17-jdk", "apt-get install -y golang",
            "aptitude install valgrind", "apt -y install rustc"],
    "dpkg": ["dpkg -i openjdk-17-jdk.deb", "dpkg --install ./pkg.deb"],
    "snap": ["snap install code --classic"],
    "flatpak": ["flatpak install flathub org.gimp.GIMP", "flatpak --user install flathub app"],
    "pip": ["pip install requests", "pip3 install --user numpy", "python3 -m pip install flask", "cd ~/projects && pip install -r requirements.txt",
            "/opt/tool/.venv/bin/pip install x --break-system-packages"],
    "npm global": ["npm install -g typescript", "npm i -g ts-node --prefix ~/.local", "npm -g install yarn", "npm install --global pnpm",
                   "pnpm add -g eslint", "pnpm install --global prettier", "yarn global add serve"],
    "cargo": ["cargo install ripgrep"],
    "go": ["go install golang.org/x/tools/gopls@latest"],
    "gem": ["gem install rails"],
    "rustup": ["rustup toolchain install nightly", "rustup-init -y"],
    "piped to a shell": ["curl -fsSL https://sh.rustup.rs | sh", "wget -qO- https://get.example.org | bash",
                         "bash <(curl -s https://example.org/install.sh)", "sh -c \"$(curl -fsSL https://example.org/i.sh)\""],
}
EVERYDAY = ["ls -la ~/projects", "python3 script.py", "cd ~/projects/app && npm run build", "npm install", "npm ci", "npm test",
            "pip list", "pip show requests", ".venv/bin/pip install -r requirements.txt",
            "source .venv/bin/activate && pip install -r requirements.txt", "python3 -m venv .venv", "cargo build --release",
            "go build ./...", "go run main.go", "apt list --installed", "apt show nodejs", "dpkg -l | grep node", "snap list",
            "gem list", "echo install", "cat install.sh", "git status", "javac Solution.java && java Solution"]


class InstallsAskFirst(unittest.TestCase):
    def test_every_kind_of_install_asks_first(self):
        for kind, commands in INSTALLS.items():
            for command in commands:
                with self.subTest(kind=kind, command=command):
                    reason = policy.approval_reason("workspace__run_command", {"command": command}, {}, RULES)
                    self.assertIsNotNone(reason)
                    self.assertTrue(reason.startswith("running `"), reason)

    def test_everyday_commands_still_run_without_asking(self):
        for command in EVERYDAY:
            with self.subTest(command=command):
                self.assertIsNone(policy.approval_reason("workspace__run_command", {"command": command}, {}, RULES))

    def test_typing_an_install_into_a_terminal_asks_too(self):
        self.assertIsNotNone(policy.approval_reason("workspace__type_text", {"text": "sudo apt install -y rustc"}, {}, RULES))

    def test_an_install_on_a_later_line_asks_too(self):
        script = "cd ~/projects/app\npip install requests"
        self.assertIsNotNone(policy.approval_reason("workspace__run_command", {"command": script}, {}, RULES))
        self.assertIsNotNone(policy.approval_reason("workspace__type_text", {"text": script}, {}, RULES))


if __name__ == "__main__":
    unittest.main()

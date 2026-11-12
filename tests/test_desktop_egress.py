import os
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "desktop-egress"
NFT = shutil.which("nft") or "/usr/sbin/nft"
LISTEN = ("import socket, sys\n"
          "server = socket.socket(socket.AF_INET6 if ':' in sys.argv[1] else socket.AF_INET)\n"
          "server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
          "server.bind((sys.argv[1], int(sys.argv[2])))\n"
          "server.listen()\n"
          "while True:\n"
          "    server.accept()[0].close()\n")
CONNECT = "import socket, sys\nsocket.create_connection((sys.argv[1], int(sys.argv[2])), timeout=2)\n"
NETWORK = r"""
set -e
mount -t tmpfs run /run
mkdir -p /run/netns
for space in vm lan inet; do ip netns add $space; ip netns exec $space ip link set lo up; done
ip link add br0 type bridge
ip link set br0 up
plug() {
  ip link add "$1" type veth peer name "$2"
  ip link set "$2" netns "$3"
  ip link set "$1" master br0
  ip link set "$1" up
  ip netns exec "$3" ip link set "$2" up
}
plug tap105i0 eth0 vm
plug lanport eth0 lan
plug inetport eth0 inet
ip addr add 192.168.50.1/24 dev br0
ip addr add 2001:db8::1/64 dev br0 nodad
ip netns exec vm ip addr add 192.168.50.10/24 dev eth0
ip netns exec vm ip addr add 203.0.113.10/24 dev eth0
ip netns exec vm ip addr add 2001:db8::10/64 dev eth0 nodad
ip netns exec lan ip addr add 192.168.50.20/24 dev eth0
ip netns exec lan ip addr add 192.168.50.53/24 dev eth0
ip netns exec lan ip addr add 2001:db8::20/64 dev eth0 nodad
ip netns exec inet ip addr add 203.0.113.5/24 dev eth0
listen() { ip netns exec "$1" python3 -c "$LISTEN" "$2" "$3" & }
listen inet 203.0.113.5 80
listen lan 192.168.50.20 8080
listen lan 192.168.50.20 9000
listen lan 192.168.50.53 53
listen lan 2001:db8::20 8080
listen vm 192.168.50.10 22
python3 -c "$LISTEN" 192.168.50.1 8006 &
python3 -c "$LISTEN" 2001:db8::1 8006 &
sleep 0.5
try() {
  if ip netns exec "$1" python3 -c "$CONNECT" "$2" "$3" 2>/dev/null; then echo "$1->$2:$3 open"; else echo "$1->$2:$3 blocked"; fi
}
round() {
  try vm 203.0.113.5 80
  try vm 192.168.50.20 8080
  try vm 192.168.50.53 53
  try vm 192.168.50.20 9000
  try lan 192.168.50.10 22
  try vm 192.168.50.1 8006
  try vm 2001:db8::20 8080
  try vm 2001:db8::1 8006
}
"$NFT" -f "$RULES"
round
"$NFT" -f "$RULES"
round
kill $(jobs -p)
"""


def can_make_namespaces() -> bool:
    if not shutil.which("unshare") or not os.path.exists(NFT):
        return False
    done = subprocess.run(["unshare", "-rnm", "sh", "-c", f"{NFT} list tables && ip link add t0 type bridge"],
                          capture_output=True)
    return done.returncode == 0


def rendered(**settings) -> subprocess.CompletedProcess:
    env = {**os.environ, "ARTY_HOSTS_FILE": "/dev/null", **settings}
    return subprocess.run([str(SCRIPT), "render"], env=env, capture_output=True, text=True)


class Render(unittest.TestCase):
    def test_bad_settings_are_refused_before_anything_is_written(self):
        for settings in ({"DESKTOP_DNS": ""}, {"DESKTOP_DNS": "1.1.1.1; flush ruleset"},
                         {"DESKTOP_DNS": "1.1.1.1", "DESKTOP_TAP": "tap105i0\" accept"},
                         {"DESKTOP_DNS": "1.1.1.1", "DESKTOP_LAN_ALLOW": "192.168.1.5"}):
            done = rendered(**settings)
            self.assertNotEqual(done.returncode, 0, settings)
            self.assertEqual(done.stdout, "", settings)

    def test_the_desktop_may_only_answer_arp_for_its_own_address(self):
        done = rendered(DESKTOP_DNS="1.1.1.1", DESKTOP_OWN_ADDRESS="192.168.50.10")
        self.assertIn("arp saddr ip != 192.168.50.10 drop", done.stdout)
        self.assertNotEqual(rendered(DESKTOP_DNS="1.1.1.1", DESKTOP_OWN_ADDRESS="vm.lan").returncode, 0)

    def test_the_rules_hook_in_after_bridge_conntrack(self):
        done = rendered(DESKTOP_DNS="1.1.1.1")
        self.assertIn("hook prerouting priority -190;", done.stdout)

    def test_unknown_commands_are_refused(self):
        done = subprocess.run([str(SCRIPT), "flush"], env={**os.environ, "ARTY_HOSTS_FILE": "/dev/null"},
                              capture_output=True, text=True)
        self.assertNotEqual(done.returncode, 0)


@unittest.skipUnless(can_make_namespaces(), "needs unprivileged user and network namespaces with nft")
class OnABridge(unittest.TestCase):
    def test_the_desktop_reaches_the_internet_and_dns_but_not_the_home_network(self):
        rules = rendered(DESKTOP_DNS="192.168.50.53", DESKTOP_LAN_ALLOW="192.168.50.20:9000")
        self.assertEqual(rules.returncode, 0, rules.stderr)
        with TemporaryDirectory(prefix="arty-egress-test-") as folder:
            rules_file = Path(folder) / "test-egress.nft"
            rules_file.write_text(rules.stdout)
            env = {**os.environ, "LISTEN": LISTEN, "CONNECT": CONNECT, "NFT": NFT, "RULES": str(rules_file)}
            done = subprocess.run(["unshare", "-rnm", "bash", "-c", NETWORK], env=env, capture_output=True, text=True,
                                  timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        expected = [
            "vm->203.0.113.5:80 open",
            "vm->192.168.50.20:8080 blocked",
            "vm->192.168.50.53:53 open",
            "vm->192.168.50.20:9000 open",
            "lan->192.168.50.10:22 open",
            "vm->192.168.50.1:8006 blocked",
            "vm->2001:db8::20:8080 blocked",
            "vm->2001:db8::1:8006 blocked",
        ]
        self.assertEqual(done.stdout.split("\n")[:16], expected + expected, "first load, then the same file loaded again")


if __name__ == "__main__":
    unittest.main()

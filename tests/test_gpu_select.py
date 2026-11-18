import os
import re
import stat
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
SELECTOR = ROOT / "bin" / "arty-gpu-select"
UNIT = ROOT / "services" / "arty-gpu-select.service"
PROVISION = ROOT / "provision.sh"
CONTAINER_FOLDER = "/etc/pve/lxc/"
CARD_PREFIX = "/dev/nvidia"
DRM_FOLDER = "/sys/class/drm"
AMD_COMPUTE_NODE = "/dev/kfd"
KFD_FOLDER = "/sys/class/kfd"
FAKE_PCT = """#!/bin/bash
echo "$*" >> "$FAKE_ROOT/pct-calls"
[ "$1" = status ] && echo "status: $FAKE_STATUS"
exit 0
"""
FAKE_LOGGER = "#!/bin/bash\nexit 0\n"
OLD_CARD_LINES = "arch: amd64\ndev0: /dev/nvidia0\ndev1: /dev/nvidiactl\n"


class TheUnitAndProvisionNameBothVendors(unittest.TestCase):
    def test_the_unit_names_nvidia_and_amd_and_says_amd_is_untested(self):
        description = re.search(r"(?m)^Description=(.*)$", UNIT.read_text()).group(1)
        for word in ("NVIDIA", "AMD", "untested"):
            self.assertIn(word, description)

    def test_the_provision_heading_names_both_and_says_amd_is_untested(self):
        heading = re.search(r'(?m)^echo "== GPU: [^\n]*', PROVISION.read_text()).group(0)
        for word in ("NVIDIA", "AMD", "untested"):
            self.assertIn(word, heading)


class RunsAgainstAFakeHost(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for inner in ("bin", "lxc", "dev"):
            (self.root / inner).mkdir()
        for name, text in (("pct", FAKE_PCT), ("logger", FAKE_LOGGER)):
            program = self.root / "bin" / name
            program.write_text(text)
            program.chmod(program.stat().st_mode | stat.S_IXUSR)
        text = SELECTOR.read_text()
        self.assertIn(CONTAINER_FOLDER, text)
        self.assertIn(CARD_PREFIX, text)
        self.selector = self.root / "arty-gpu-select"
        self.selector.write_text(text.replace(CONTAINER_FOLDER, f"{self.root}/lxc/").replace(CARD_PREFIX, f"{self.root}/dev/nvidia")
                                 .replace(DRM_FOLDER, f"{self.root}/sys/class/drm").replace(AMD_COMPUTE_NODE, f"{self.root}/dev/kfd")
                                 .replace(KFD_FOLDER, f"{self.root}/sys/class/kfd"))
        self.conf = self.root / "lxc" / "104.conf"
        self.settings = self.root / "arty-gpu-select.settings"

    def select(self, container_state: str = "stopped", *arguments, **extra) -> subprocess.CompletedProcess:
        environment = {**os.environ, "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}", "FAKE_ROOT": str(self.root),
                       "FAKE_STATUS": container_state, "CT": "104", "ARTY_GPU_SETTINGS": str(self.settings), **extra}
        for name in ("CT_RENDER_GID", "CT_VIDEO_GID"):
            if name not in extra:
                environment.pop(name, None)
        return subprocess.run(["bash", str(self.selector), *arguments], env=environment, capture_output=True, text=True,
                              timeout=30)

    def kfd_node(self, number: int, version: int):
        node = self.root / "sys" / "class" / "kfd" / "kfd" / "topology" / "nodes" / str(number)
        node.mkdir(parents=True)
        (node / "properties").write_text(f"cpu_cores_count 0\ngfx_target_version {version}\n")

    def pct_calls(self) -> list[str]:
        calls = self.root / "pct-calls"
        return calls.read_text().splitlines() if calls.exists() else []

    def fit_card(self):
        for name in ("nvidia0", "nvidiactl"):
            (self.root / "dev" / name).symlink_to("/dev/null")

    def fit_amd_card(self, with_kfd: bool = True, driver: str = "amdgpu"):
        device = self.root / "sys" / "devices" / "pci0000:00" / "0000:03:00.0"
        for node in ("card1", "renderD128"):
            (device / "drm" / node).mkdir(parents=True)
        driver_folder = self.root / "sys" / "bus" / "pci" / "drivers" / driver
        driver_folder.mkdir(parents=True)
        (device / "driver").symlink_to(driver_folder)
        drm = self.root / "sys" / "class" / "drm"
        drm.mkdir(parents=True)
        (drm / "renderD128").mkdir()
        (drm / "renderD128" / "device").symlink_to(device)
        if with_kfd:
            (self.root / "dev" / "kfd").symlink_to("/dev/null")

    def test_with_no_card_a_stopped_container_loses_its_old_card_devices(self):
        self.conf.write_text(OLD_CARD_LINES)
        finished = self.select()
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertIn("switching to no GPU", finished.stdout)
        self.assertEqual(["status 104", "set 104 -delete dev0,dev1"], self.pct_calls())

    def test_with_no_card_and_no_devices_nothing_changes(self):
        self.conf.write_text("arch: amd64\n")
        finished = self.select()
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertIn("already has the no GPU", finished.stdout)
        self.assertEqual([], self.pct_calls())

    def test_a_running_container_is_never_changed(self):
        self.conf.write_text(OLD_CARD_LINES)
        finished = self.select("running")
        self.assertEqual(1, finished.returncode)
        self.assertIn("stop it", finished.stdout)
        self.assertEqual(["status 104"], self.pct_calls())

    def test_a_dry_run_only_says_what_it_would_do(self):
        self.conf.write_text(OLD_CARD_LINES)
        finished = self.select("stopped", "--dry-run")
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertIn("switching to no GPU", finished.stdout)
        self.assertEqual([], self.pct_calls())

    def test_a_fitted_card_is_handed_to_the_container(self):
        self.fit_card()
        self.conf.write_text("arch: amd64\n")
        finished = self.select()
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertEqual(["status 104", f"set 104 -dev0 {self.root}/dev/nvidia0 -dev1 {self.root}/dev/nvidiactl"],
                         self.pct_calls())

    def test_devices_in_a_snapshot_section_are_not_the_containers_own(self):
        self.fit_card()
        self.conf.write_text(f"arch: amd64\ndev0: {self.root}/dev/nvidia0\ndev1: {self.root}/dev/nvidiactl\n\n"
                             "[before-gpu]\narch: amd64\ndev0: /dev/dri/card0\n")
        finished = self.select()
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertIn("already has the NVIDIA", finished.stdout)
        self.assertEqual([], self.pct_calls())

    def test_a_missing_container_config_stops_before_anything_is_changed(self):
        self.fit_card()
        finished = self.select()
        self.assertNotEqual(0, finished.returncode)
        self.assertEqual([], [call for call in self.pct_calls() if call.startswith("set")])

    def test_an_amd_card_with_kfd_gets_kfd_and_its_render_and_card_nodes(self):
        self.fit_amd_card()
        self.conf.write_text("arch: amd64\n")
        finished = self.select()
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertIn("switching to AMD (ROCm", finished.stdout)
        self.assertEqual(["status 104", f"set 104 -dev0 {self.root}/dev/kfd,gid=992 -dev1 /dev/dri/renderD128,gid=992 "
                                        "-dev2 /dev/dri/card1,gid=44"], self.pct_calls())

    def test_an_amd_card_without_kfd_gets_only_its_render_and_card_nodes(self):
        self.fit_amd_card(with_kfd=False)
        self.conf.write_text(OLD_CARD_LINES)
        finished = self.select()
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertIn("switching to AMD (Vulkan only", finished.stdout)
        self.assertEqual(["status 104", "set 104 -delete dev0,dev1",
                          "set 104 -dev0 /dev/dri/renderD128,gid=992 -dev1 /dev/dri/card1,gid=44"], self.pct_calls())

    def test_an_amd_card_already_handed_over_changes_nothing(self):
        self.fit_amd_card()
        self.conf.write_text(f"arch: amd64\ndev0: {self.root}/dev/kfd,gid=992\ndev1: /dev/dri/renderD128,gid=992\n"
                             "dev2: /dev/dri/card1,gid=44\n")
        finished = self.select()
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertIn("already has the AMD (ROCm", finished.stdout)
        self.assertEqual([], self.pct_calls())

    def test_the_container_s_group_ids_can_be_given(self):
        self.fit_amd_card()
        self.conf.write_text("arch: amd64\n")
        finished = self.select(CT_RENDER_GID="993", CT_VIDEO_GID="45")
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertEqual(f"set 104 -dev0 {self.root}/dev/kfd,gid=993 -dev1 /dev/dri/renderD128,gid=993 -dev2 /dev/dri/card1,gid=45",
                         self.pct_calls()[-1])

    def test_group_ids_in_the_settings_file_hold_for_a_run_by_hand(self):
        self.fit_amd_card()
        self.conf.write_text("arch: amd64\n")
        self.settings.write_text("CT_RENDER_GID=993\nCT_VIDEO_GID=45\n")
        finished = self.select("stopped", "--dry-run")
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertIn(f"{self.root}/dev/kfd,gid=993 /dev/dri/renderD128,gid=993 /dev/dri/card1,gid=45", finished.stdout)
        given = self.select("stopped", "--dry-run", CT_RENDER_GID="994")
        self.assertIn("renderD128,gid=994 /dev/dri/card1,gid=45", given.stdout)

    def test_a_group_id_that_is_not_a_number_stops_before_anything_is_changed(self):
        self.fit_amd_card()
        self.conf.write_text("arch: amd64\n")
        self.settings.write_text("CT_RENDER_GID=render\n")
        finished = self.select()
        self.assertEqual(1, finished.returncode)
        self.assertIn("is not a number", finished.stderr)
        self.assertEqual([], self.pct_calls())

    def test_gfx_target_names_the_card_s_target_or_nothing(self):
        finished = self.select("running", "--gfx-target")
        self.assertEqual((0, ""), (finished.returncode, finished.stdout), finished.stderr)
        self.kfd_node(0, 0)
        self.kfd_node(1, 110000)
        finished = self.select("running", "--gfx-target")
        self.assertEqual((0, "gfx1100\n"), (finished.returncode, finished.stdout), finished.stderr)
        self.assertEqual([], self.pct_calls())

    def test_an_nvidia_card_wins_over_an_amd_card(self):
        self.fit_card()
        self.fit_amd_card()
        self.conf.write_text("arch: amd64\n")
        finished = self.select()
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertEqual(["status 104", f"set 104 -dev0 {self.root}/dev/nvidia0 -dev1 {self.root}/dev/nvidiactl"],
                         self.pct_calls())

    def test_another_vendors_render_node_is_not_handed_over(self):
        self.fit_amd_card(driver="i915")
        self.conf.write_text("arch: amd64\n")
        finished = self.select()
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertIn("already has the no GPU", finished.stdout)
        self.assertEqual([], self.pct_calls())

    def test_kind_names_the_card_without_reading_or_changing_the_container(self):
        cases = (("none", lambda: None), ("amd-vulkan", lambda: self.fit_amd_card(with_kfd=False)),
                 ("amd-rocm", lambda: (self.root / "dev" / "kfd").symlink_to("/dev/null")), ("nvidia", self.fit_card))
        for expected, fit in cases:
            with self.subTest(kind=expected):
                fit()
                finished = self.select("running", "--kind")
                self.assertEqual((0, f"{expected}\n"), (finished.returncode, finished.stdout), finished.stderr)
        self.assertEqual([], self.pct_calls())
        self.assertFalse(self.conf.exists())



INSTALLED_SELECTOR = "/usr/local/sbin/arty-gpu-select"
GPU_BLOCK = re.compile(r'(?ms)^echo "== GPU: .*?(?=^echo "== )')
FAKE_PROVISION_PCT = """#!/bin/bash
echo "$*" >> "$FAKE_ROOT/pct-calls"
[ "$1" = exec ] && cat > "$FAKE_ROOT/pct-stdin-$(wc -l < "$FAKE_ROOT/pct-calls")"
exit "${FAKE_PCT_EXIT:-0}"
"""


FAKE_VENV_PYTHON = """#!/bin/bash
[ "$1" = -m ] && [ "$2" = venv ] || exit 1
mkdir -p "$3/bin"
printf '%s' "$FAKE_PIP" > "$3/bin/pip"
chmod 755 "$3/bin/pip"
"""
FAKE_ROCM_PIP = """#!/bin/bash
packages="$(dirname "$0")/../lib/python3.13/site-packages"
mkdir -p "$packages/_rocm_sdk_core/lib" "$packages/_rocm_sdk_libraries/lib"
touch "$packages/_rocm_sdk_core/lib/libamdhip64.so.7" "$packages/_rocm_sdk_libraries/lib/libhipblas.so.3" \\
  "$packages/_rocm_sdk_libraries/lib/librocblas.so.5"
target=$(echo "$*" | grep -o 'device-gfx[0-9a-f]*' | sed 's/device-//')
if echo " $FAKE_OFFERED " | grep -q " $target "; then
  mkdir -p "$packages/rocm_sdk_device_$target" "$packages/rocm_sdk_device_$target-10.0.0.dist-info"
else
  echo "WARNING: rocm 10.0.0 does not provide the extra 'device-$target'" >&2
fi
"""


class ProvisionInstallsAmdPiecesOnlyForAnAmdCard(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / "bin").mkdir()
        for name, text in (("pct", FAKE_PROVISION_PCT), ("systemctl", "#!/bin/bash\nexit 0\n")):
            program = self.root / "bin" / name
            program.write_text(text)
            program.chmod(0o755)
        self.selector = self.root / "arty-gpu-select"
        self.selector.write_text('#!/bin/bash\n[ "$1" = --kind ] && echo "$FAKE_KIND"\n'
                                 '[ "$1" = --gfx-target ] && echo "${FAKE_GFX:-}"\nexit 0\n')
        self.block = GPU_BLOCK.search(PROVISION.read_text()).group(0).replace(INSTALLED_SELECTOR, str(self.selector))

    def provision(self, kind: str, **extra) -> subprocess.CompletedProcess:
        environment = {**os.environ, "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}", "FAKE_ROOT": str(self.root),
                       "FAKE_KIND": kind, **extra}
        return subprocess.run(["bash", "-c", f"set -euo pipefail\nCT=104\n{self.block}"], env=environment,
                              capture_output=True, text=True, timeout=30)

    def pct_calls(self) -> list[str]:
        calls = self.root / "pct-calls"
        return calls.read_text().splitlines() if calls.exists() else []

    def test_nvidia_and_no_card_install_nothing_in_the_container(self):
        for kind in ("nvidia", "none"):
            with self.subTest(kind=kind):
                finished = self.provision(kind)
                self.assertEqual(0, finished.returncode, finished.stderr)
                self.assertEqual([], self.pct_calls())

    def test_an_amd_card_with_kfd_gets_the_rocm_libraries_its_kernels_and_the_vulkan_fallback(self):
        finished = self.provision("amd-rocm", FAKE_GFX="gfx1100")
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertIn("untested: the kernel package is checked by a folder named rocm_sdk_device_gfx1100", finished.stdout)
        calls = self.pct_calls()
        self.assertEqual(2, len(calls))
        self.assertIn("apt-get install -y -qq libvulkan1 mesa-vulkan-drivers", calls[0])
        self.assertEqual("exec 104 -- bash -s -- 10.0.0 gfx1100", calls[1])
        script = (self.root / "pct-stdin-2").read_text()
        self.assertIn('--no-cache-dir --index-url https://stable.repo.amd.com/rocm/whl-next/ "rocm[libraries,device-$2]==$1"',
                      script)
        for library in ("libamdhip64.so.7", "libhipblas.so.3", "librocblas.so.5"):
            self.assertIn(library, script)
        self.assertIn("list both in runtimes.rocm.library_path", script)
        self.assertIn("folder=/srv/ai/arty-engine/rocm-$(echo", script)

    def test_a_card_target_the_rocm_build_does_not_carry_gets_the_vulkan_build_only(self):
        finished = self.provision("amd-rocm", FAKE_GFX="gfx803")
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertEqual(1, len(self.pct_calls()))
        self.assertIn("libvulkan1", self.pct_calls()[0])
        self.assertIn("gfx803", finished.stdout)
        self.assertIn("Vulkan only", finished.stdout)

    def run_rocm_step(self, gfx_target: str, offered_targets: str, left_by_another_card: str = "") -> subprocess.CompletedProcess:
        self.provision("amd-rocm", FAKE_GFX=gfx_target)
        container = self.root / "container"
        (container / "bin").mkdir(parents=True)
        for name, text in (("apt-get", "#!/bin/bash\nexit 0\n"), ("python3", FAKE_VENV_PYTHON)):
            program = container / "bin" / name
            program.write_text(text)
            program.chmod(0o755)
        packages = container / "rocm-10.0" / "lib" / "python3.13" / "site-packages"
        if left_by_another_card:
            (packages / f"rocm_sdk_device_{left_by_another_card}").mkdir(parents=True)
        step = (self.root / "pct-stdin-2").read_text().replace("/srv/ai/arty-engine", str(container))
        environment = {**os.environ, "PATH": f"{container / 'bin'}:{os.environ['PATH']}", "FAKE_PIP": FAKE_ROCM_PIP,
                       "FAKE_OFFERED": offered_targets}
        return subprocess.run(["bash", "-s", "--", "10.0.0", gfx_target], input=step, env=environment, capture_output=True,
                              text=True, timeout=30)

    def test_the_rocm_step_passes_when_the_card_s_kernel_package_was_installed(self):
        finished = self.run_rocm_step("gfx1100", "gfx1100 gfx1030")
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertIn("list both in runtimes.rocm.library_path", finished.stdout)

    def test_the_rocm_step_fails_when_no_kernel_package_for_the_card_was_installed(self):
        finished = self.run_rocm_step("gfx1100", "gfx1030")
        self.assertEqual(1, finished.returncode, finished.stdout)
        self.assertIn("no ROCm kernel package for gfx1100", finished.stderr)
        self.assertIn("(rocm_sdk_device_gfx1100) is untested on a real card", finished.stderr)

    def test_another_card_s_kernel_package_left_in_the_folder_does_not_count(self):
        finished = self.run_rocm_step("gfx1100", "gfx1030", left_by_another_card="gfx1030")
        self.assertEqual(1, finished.returncode, finished.stdout)
        self.assertIn("no ROCm kernel package for gfx1100", finished.stderr)

    def test_no_kernel_package_is_guessed_when_the_card_s_target_cannot_be_read(self):
        finished = self.provision("amd-rocm")
        self.assertEqual(0, finished.returncode, finished.stderr)
        self.assertIn("no ROCm kernel package is guessed", finished.stdout)
        self.assertEqual(1, len(self.pct_calls()))
        self.assertIn("libvulkan1", self.pct_calls()[0])

    def test_an_amd_card_without_kfd_gets_the_vulkan_loader_and_driver(self):
        finished = self.provision("amd-vulkan")
        self.assertEqual(0, finished.returncode, finished.stderr)
        calls = self.pct_calls()
        self.assertEqual(1, len(calls))
        self.assertTrue(calls[0].startswith("exec 104 -- bash -c "))
        self.assertIn("apt-get install -y -qq libvulkan1 mesa-vulkan-drivers", calls[0])

    def test_a_failed_amd_install_warns_and_provisioning_goes_on(self):
        for kind in ("amd-rocm", "amd-vulkan"):
            with self.subTest(kind=kind):
                finished = self.provision(kind, FAKE_PCT_EXIT="1", FAKE_GFX="gfx1100")
                self.assertEqual(0, finished.returncode, finished.stderr)
                self.assertIn("WARNING", finished.stdout)


if __name__ == "__main__":
    unittest.main()

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "downloads" / "build_release.py"
README = ROOT / "README.md"
FLAG = re.compile(r"""add_argument\(\s*["'](--[\w-]+)["']""")


class ReleaseFlagsInTheReadme(unittest.TestCase):
    def test_every_release_builder_flag_is_described_in_the_readme(self):
        if not BUILDER.exists():
            self.skipTest("the release builder is not in this copy")
        flags = FLAG.findall(BUILDER.read_text())
        self.assertIn("--adopt", flags)
        readme = README.read_text()
        missing = [flag for flag in flags if f"`{flag}`" not in readme]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()

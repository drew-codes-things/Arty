import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("BRAIN_FILES", tempfile.mkdtemp(prefix="arty-documents-"))

import sharing


class DocumentFormatTest(unittest.TestCase):
    def test_aliases_and_extensions(self):
        self.assertEqual(sharing.document_format("Word"), "docx")
        self.assertEqual(sharing.document_format(".PDF"), "pdf")
        self.assertEqual(sharing.document_format("slides"), "pptx")
        self.assertEqual(sharing.document_format("excel"), "xlsx")
        self.assertIsNone(sharing.document_format("psd"))

    def test_name_is_safe(self):
        name = sharing.document_name("What cotton is / made from?", "docx")
        self.assertRegex(name, r"^What-cotton-is-made-from-\d{4}-\d{2}-\d{2}-\d{6}\.docx$")
        self.assertTrue(sharing.document_name("!!!", "pdf").startswith("document-"))


class TableRowsTest(unittest.TestCase):
    def test_markdown_table(self):
        content = "Prices:\n\n| Item | Cost |\n|---|---:|\n| Tea | 2.50 |\n| Milk | 1,200 |\n"
        self.assertEqual(sharing.table_rows(content), [["Item", "Cost"], ["Tea", 2.5], ["Milk", 1200]])

    def test_csv_text(self):
        self.assertEqual(sharing.table_rows('name,age\n"Smith, J",41\n\n'), [["name", "age"], ["Smith, J", 41]])

    def test_codes_with_leading_text_stay_text(self):
        self.assertEqual(sharing.table_rows("code\nAB12"), [["code"], ["AB12"]])


class MakeDocumentTest(unittest.TestCase):
    def setUp(self):
        self.commands = []
        patcher = unittest.mock.patch.object(sharing.remote, "text", side_effect=self.fake_remote)
        patcher.start()
        self.addCleanup(patcher.stop)
        sender = unittest.mock.patch.object(sharing, "send_file", side_effect=lambda path: f"Sent {path}")
        self.sent = sender.start()
        self.addCleanup(sender.stop)

    def fake_remote(self, command, stdin=None, timeout=None):
        self.commands.append((command, stdin))
        return 0, ""

    def test_word_document_goes_through_pandoc_and_is_sent(self):
        result = sharing.make_document("Cotton", "# Cotton\n\nIt comes from seed pods.", "word")
        self.assertTrue(result.startswith("Sent ~/Documents/Arty/Cotton-"))
        self.assertTrue(any(command.startswith("cat > ") and stdin == b"# Cotton\n\nIt comes from seed pods."
                            for command, stdin in self.commands))
        pandoc = next(command for command, _ in self.commands if command.startswith("pandoc"))
        self.assertIn(".docx", pandoc)
        self.assertNotIn("--metadata", pandoc)

    def test_spreadsheet_sends_rows_to_openpyxl(self):
        sharing.make_document("Budget", "| a | b |\n|---|---|\n| 1 | 2 |", "xlsx")
        command, stdin = next((c, s) for c, s in self.commands if "python3 -c" in c)
        self.assertIn(b'"rows": [["a", "b"], [1, 2]]', stdin)
        self.assertIn(b'"sheet": "Budget"', stdin)

    def test_spreadsheet_needs_a_table(self):
        self.assertIn("no table", sharing.make_document("Budget", "|---|---|", "csv"))

    def test_unknown_format_and_empty_content(self):
        self.assertIn("can't make a 'psd' file", sharing.make_document("x", "y", "psd"))
        self.assertIn("content first", sharing.make_document("x", "  ", "pdf"))
        self.assertEqual(self.commands, [])

    def test_desktop_failure_is_reported_not_sent(self):
        with unittest.mock.patch.object(sharing.remote, "text", return_value=(1, "pandoc: unknown option")):
            result = sharing.make_document("x", "hello", "odt")
        self.assertEqual(result, "Couldn't make the odt file: pandoc: unknown option")
        self.sent.assert_not_called()


if __name__ == "__main__":
    unittest.main()

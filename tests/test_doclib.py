import gzip
import io
import ipaddress
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
import unittest.mock
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from doclib import build, convert, fetch, refresh, search, server  # noqa: E402
from doclib.sources import Source  # noqa: E402

SPHINX_PAGE = """<html><head><title>What's new in Python 3.14 \u2014 Python 3.14 documentation</title></head><body>
<div class="sphinxsidebar"><h3>Navigation</h3><p>sidebar words template</p></div>
<div class="body" role="main">
<section id="whats-new"><h1>What's new in Python 3.14<a class="headerlink" href="#x">¶</a></h1>
<section id="whatsnew314-template-string-literals"><h2>PEP 750: Template string literals</h2>
<p>Template strings return a <code>Template</code> object, not a <code>str</code>.</p>
<div class="highlight-python3 notranslate"><div class="highlight"><pre><span class="k">def</span> lower_upper(template):
    for part in template:
        if isinstance(part, Interpolation):
            pass
</pre></div></div>
</section>
<section id="other"><h2>Other language changes</h2><p>Smaller changes to the interpreter.</p></section>
</section></div><div class="footer">Copyright footer text</div></body></html>"""

API_PAGE = """<div role="main"><h1>Built-in Types</h1>
<dl class="py method"><dt class="sig" id="str.removeprefix">str.removeprefix(<em>prefix</em>, /)</dt>
<dd><p>Return a string with the prefix removed.</p><pre data-language="python">&gt;&gt;&gt; 'TestHook'.removeprefix('Test')
'Hook'</pre></dd></dl></div>"""

PEP_FINAL = """PEP: 750
Title: Template Strings
Status: Final
Python-Version: 3.14

Abstract
========

Template strings are a generalization of f-strings::

    template = t"Hello {name}"

Examples
========

Example: HTML Templating
------------------------

.. code-block:: python

    def html(template):
        return "".join(part for part in template)

Copyright
=========

Public domain.
"""

PEP_REJECTED = """PEP: 501
Title: General purpose template literal strings
Status: Withdrawn

Abstract
========

Template literal strings with a render method.
"""


def tar_bytes(files: dict[str, str], mode: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode=mode) as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def write_gzip_json(path: Path, value):
    with gzip.open(path, "wt", encoding="utf-8") as opened:
        json.dump(value, opened)


TEST_SOURCES = (
    Source("python-3.14", "python", "3.14", "python", "3.14", "Python 3.14"),
    Source("python-3.13", "python", "3.13", "python", "3.13", "Python 3.13"),
    Source("peps", "python", "", "peps", "unused", "Python PEPs"),
    Source("go", "go", "", "devdocs", "go", "Go"),
)

GO_PAGE = """<h1>Package strings</h1><h3 id="Builder">type Builder</h3>
<p>A Builder is used to efficiently build a string using Write methods.</p>
<pre class="language-go">var b strings.Builder
b.WriteString("hello")</pre>
<h3 id="Cut">func Cut</h3><p>Cut slices s around the first instance of sep.</p>"""


def make_raw(raw: Path):
    for key, version, text in (("python-3.14", "3.14", SPHINX_PAGE), ("python-3.13", "3.13", SPHINX_PAGE.replace("3.14", "3.13"))):
        folder = raw / key
        folder.mkdir(parents=True)
        files = {f"python-{version}-docs-html/whatsnew/{version}.html": text,
                 f"python-{version}-docs-html/library/stdtypes.html": API_PAGE,
                 f"python-{version}-docs-html/genindex-all.html": "<div role='main'><h1>Index</h1><p>template</p></div>"}
        (folder / "docs-html.tar.bz2").write_bytes(tar_bytes(files, "w:bz2"))
    peps = raw / "peps"
    peps.mkdir()
    (peps / "peps.tar.gz").write_bytes(tar_bytes({"peps-main/peps/pep-0750.rst": PEP_FINAL,
                                                  "peps-main/peps/pep-0501.rst": PEP_REJECTED}, "w:gz"))
    go = raw / "go"
    go.mkdir()
    write_gzip_json(go / "db.json.gz", {"strings/index": GO_PAGE, "index": "<h1>Go</h1>"})
    write_gzip_json(go / "index.json.gz", {"entries": [{"name": "strings", "path": "strings/index", "type": "strings"}]})
    (go / "meta.json").write_text(json.dumps({"attribution": "&copy; Google, Inc. Licensed under the BSD.", "release": "1.27"}))


class HtmlConversion(unittest.TestCase):
    def test_sections_follow_headings_and_keep_code_whole(self):
        sections = convert.html_sections(SPHINX_PAGE)
        headings = [section.heading for section in sections]
        self.assertIn("What's new in Python 3.14 > PEP 750: Template string literals", headings)
        tstrings = sections[headings.index("What's new in Python 3.14 > PEP 750: Template string literals")]
        self.assertEqual(tstrings.anchor, "whatsnew314-template-string-literals")
        self.assertIn("```python3\ndef lower_upper(template):\n    for part in template:\n        if isinstance(part, Interpolation):",
                      tstrings.body)
        self.assertIn("`Template` object", tstrings.body)

    def test_only_the_main_part_of_a_page_is_kept(self):
        text = "\n".join(section.body for section in convert.html_sections(SPHINX_PAGE))
        self.assertNotIn("sidebar words", text)
        self.assertNotIn("Copyright footer", text)
        self.assertNotIn("¶", "".join(section.heading for section in convert.html_sections(SPHINX_PAGE)))

    def test_api_entries_become_their_own_sections(self):
        sections = convert.html_sections(API_PAGE)
        entry = next(section for section in sections if section.anchor == "str.removeprefix")
        self.assertIn("str.removeprefix(prefix, /)", entry.heading)
        self.assertIn("```python\n>>> 'TestHook'.removeprefix('Test')\n'Hook'\n```", entry.body)

    def test_long_sections_are_split_between_blocks(self):
        paragraphs = "".join(f"<p>{'word ' * 150}{number}</p>" for number in range(20))
        sections = convert.html_sections(f"<h1>Long</h1>{paragraphs}<pre>code()</pre>")
        self.assertGreater(len(sections), 1)
        self.assertTrue(all(len(section.body) <= convert.SECTION_CHARS + 800 for section in sections))
        self.assertTrue(sections[-1].heading.endswith(f"(part {len(sections)})"))
        self.assertIn("```\ncode()\n```", sections[-1].body)


class RstConversion(unittest.TestCase):
    def test_pep_fields_titles_and_code_blocks(self):
        fields, sections = convert.rst_document(PEP_FINAL)
        self.assertEqual(fields["Status"], "Final")
        self.assertEqual(fields["Python-Version"], "3.14")
        by_heading = {section.heading: section for section in sections}
        self.assertIn('```\ntemplate = t"Hello {name}"\n```', by_heading["Template Strings > Abstract"].body)
        self.assertIn("generalization of f-strings:", by_heading["Template Strings > Abstract"].body)
        html = by_heading["Template Strings > Examples > Example: HTML Templating"]
        self.assertEqual(html.anchor, "example-html-templating")
        self.assertIn('```python\ndef html(template):\n    return "".join(part for part in template)\n```', html.body)

    def test_inline_markup_becomes_plain(self):
        self.assertEqual(convert.rst_inline(":class:`~string.templatelib.Template` and ``str`` and `PEP <https://x>`_"),
                         "string.templatelib.Template and `str` and PEP")


class BuildAndSearch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        root = Path(cls.folder.name)
        make_raw(root / "raw")
        cls.database = root / "docs.db"
        cls.counts = build.build(list(TEST_SOURCES), root / "raw", cls.database)
        cls.patch = unittest.mock.patch.object(search, "SOURCES", TEST_SOURCES)
        cls.patch.start()

    @classmethod
    def tearDownClass(cls):
        cls.patch.stop()
        cls.folder.cleanup()

    def test_every_source_is_indexed_and_withdrawn_peps_and_index_pages_are_not(self):
        self.assertEqual(set(self.counts), {"python-3.14", "python-3.13", "peps", "go"})
        hits = search.search(self.database, "python general purpose template literal render method", 10)
        self.assertFalse(any("pep-0501" in hit.url for hit in hits))
        self.assertFalse(any("genindex" in hit.url for hit in hits))

    def test_a_named_version_is_preferred(self):
        newest = search.search(self.database, "Python 3.14 template string literals lower_upper", 3)
        self.assertTrue(newest[0].url.startswith("https://docs.python.org/3.14/whatsnew/3.14.html#whatsnew314"))
        older = search.search(self.database, "Python 3.13 template string literals lower_upper", 3)
        self.assertTrue(older[0].url.startswith("https://docs.python.org/3.13/"))

    def test_without_a_version_the_newest_docs_are_searched(self):
        hits = search.search(self.database, "python str.removeprefix", 3)
        self.assertEqual(hits[0].url, "https://docs.python.org/3.14/library/stdtypes.html#str.removeprefix")
        self.assertFalse(any("/3.13/" in hit.url for hit in hits))

    def test_a_named_language_narrows_the_search(self):
        hits = search.search(self.database, "Go strings.Builder example", 3)
        self.assertEqual(hits[0].url, "https://devdocs.io/go/strings/index#Builder")
        self.assertTrue(all(hit.family == "go" for hit in hits))

    def test_read_returns_the_whole_section_with_its_licence(self):
        hit = search.search(self.database, "Go strings.Builder", 1)[0]
        section = search.read(self.database, hit.id)
        self.assertIn('```go\nvar b strings.Builder\nb.WriteString("hello")\n```', section["body"])
        self.assertIn("Licensed under the BSD", section["licence"])

    def test_a_missing_devdocs_licence_is_filled_in(self):
        for location in ("http", "man"):
            with self.subTest(location=location):
                source = Source(location, "linux", "", "devdocs", location, location)
                self.assertEqual(build.licence_of(source, {"attribution": "..."}), build.DEVDOCS_MISSING_LICENCES[location])
        self.assertIn("BSD", build.licence_of(TEST_SOURCES[3], {"attribution": "Licensed under the BSD."}))

    def test_nothing_to_search_for_gives_nothing(self):
        self.assertEqual(search.search(self.database, "how do I?", 3), [])

    def test_the_build_replaces_the_database_only_when_finished(self):
        self.assertFalse(self.database.with_name("docs.db.new").exists())

    def test_the_build_records_each_sources_id_range(self):
        with sqlite3.connect(self.database) as connection:
            stored = connection.execute("SELECT key, first_id, last_id FROM sources ORDER BY key").fetchall()
            actual = connection.execute("SELECT source, min(id), max(id) FROM sections GROUP BY source ORDER BY source").fetchall()
        self.assertEqual(actual, stored)

    def test_sources_are_filtered_by_id_range_without_reading_every_matching_section(self):
        statements = []
        real_connect = search.connect

        def traced(database):
            connection = real_connect(database)
            connection.set_trace_callback(statements.append)
            return connection

        with unittest.mock.patch.object(search, "connect", side_effect=traced):
            hits = search.search(self.database, "Go strings.Builder example", 3)
        self.assertEqual(hits[0].url, "https://devdocs.io/go/strings/index#Builder")
        ranked = [statement for statement in statements if "MATCH" in statement]
        self.assertTrue(ranked and all("rowid BETWEEN" in statement and "s.source IN" not in statement for statement in ranked))

    def test_an_index_built_before_id_ranges_gives_the_same_results(self):
        older = Path(self.folder.name) / "older.db"
        shutil.copy(self.database, older)
        with sqlite3.connect(older) as connection:
            connection.execute("ALTER TABLE sources DROP COLUMN first_id")
            connection.execute("ALTER TABLE sources DROP COLUMN last_id")
        for question in ("Go strings.Builder example", "Python 3.13 template string literals lower_upper", "python str.removeprefix"):
            with self.subTest(question=question):
                self.assertEqual(search.search(self.database, question, 5), search.search(older, question, 5))


CSHARP_PAGE = """---
title: "using statement - ensure the correct use of disposable objects"
description: The using statement
---
# using statement (C# Reference)

The `using` statement ensures the correct use of an <xref:System.IDisposable?displayProperty=nameWithType> instance, see \
[the guide](../../fundamentals/index.md):

:::code language="csharp" source="snippets/using/Program.cs" id="Using":::

[!INCLUDE[note](../includes/version-note.md)]

> [!NOTE]
> Prefer a using declaration.

## Using declaration

  :::code language="csharp" source="snippets/using/Program.cs" id="UsingDeclaration":::

[!code-csharp[Legacy](~/samples/snippets/csharp/keywords/Legacy.cs#Shown)]

```csharp
# not a heading inside code
```

:::image type="content" source="media/x.png" alt-text="diagram":::
"""

CSHARP_SNIPPETS = """namespace Demo;

public static class Program
{
    public static void Main()
    {
        // <Using>
        using (var reader = File.OpenText("numbers.txt"))
        {
            Console.WriteLine(reader.ReadLine());
        }
        // </Using>

        // <UsingDeclaration>
        using var writer = File.CreateText("out.txt");
        // </UsingDeclaration>
    }
}
"""

CSHARP_FILES = {
    "docs/csharp/language-reference/statements/using.md": CSHARP_PAGE,
    "docs/csharp/language-reference/statements/snippets/using/Program.cs": CSHARP_SNIPPETS,
    "docs/csharp/language-reference/includes/version-note.md": "---\nms.topic: include\n---\nAvailable since C# 8.\n",
    "samples/snippets/csharp/keywords/Legacy.cs": "class Legacy\n{\n    // <Shown>\n    int value = 1;\n    // </Shown>\n}\n",
    "docs/csharp/index.md": "---\ntitle: C# docs\n---\n# C# documentation\n\nLearn to write C# with records.\n",
}


class MarkdownConversion(unittest.TestCase):
    def setUp(self):
        self.title, self.sections = convert.markdown_document(
            CSHARP_PAGE, "docs/csharp/language-reference/statements/using.md", CSHARP_FILES)
        self.text = "\n\n".join(section.body for section in self.sections)

    def test_headings_become_sections_under_the_page_title(self):
        self.assertEqual("using statement (C# Reference)", self.title)
        self.assertEqual(["using statement (C# Reference)", "using statement (C# Reference) > Using declaration"],
                         [section.heading for section in self.sections])
        self.assertEqual("using-declaration", self.sections[1].anchor)

    def test_code_includes_are_resolved_to_their_snippet_region(self):
        self.assertIn('```csharp\nusing (var reader = File.OpenText("numbers.txt"))\n{', self.text)
        self.assertIn('```csharp\nusing var writer = File.CreateText("out.txt");\n```', self.text)
        self.assertIn("```csharp\nint value = 1;\n```", self.text)
        self.assertNotIn("// <Using>", self.text)
        self.assertNotIn("public static class Program", self.text)

    def test_includes_links_and_alerts_become_plain_text(self):
        self.assertIn("Available since C# 8.", self.text)
        self.assertIn("System.IDisposable instance", self.text)
        self.assertIn("see the guide:", self.text)
        self.assertIn("Note:\nPrefer a using declaration.", self.text)
        self.assertNotIn(":::", self.text)
        self.assertNotIn("ms.topic", self.text)

    def test_hashes_inside_code_are_not_headings(self):
        self.assertIn("```csharp\n# not a heading inside code\n```", self.text)


class CsharpSource(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        root = Path(cls.folder.name)
        folder = root / "raw" / "csharp"
        folder.mkdir(parents=True)
        (folder / fetch.GIT_ARCHIVE).write_bytes(tar_bytes(CSHARP_FILES, "w:gz"))
        (folder / "meta.json").write_text(json.dumps({"commit": "abc123", "release": "abc123"}))
        cls.source = Source("csharp", "csharp", "", "dotnet", "unused", "C# (.NET docs)")
        cls.database = root / "docs.db"
        cls.counts = build.build([cls.source], root / "raw", cls.database)
        cls.patch = unittest.mock.patch.object(search, "SOURCES", (cls.source,))
        cls.patch.start()

    @classmethod
    def tearDownClass(cls):
        cls.patch.stop()
        cls.folder.cleanup()

    def test_pages_are_indexed_with_their_learn_urls_and_includes_are_not_pages(self):
        with sqlite3.connect(self.database) as connection:
            urls = sorted(row[0] for row in connection.execute("SELECT url FROM sections"))
        self.assertIn("https://learn.microsoft.com/en-us/dotnet/csharp/", urls)
        self.assertIn("https://learn.microsoft.com/en-us/dotnet/csharp/language-reference/statements/using#using-declaration",
                      urls)
        self.assertFalse(any("includes" in url for url in urls))

    def test_a_csharp_question_finds_the_page_with_its_code_and_licence(self):
        hit = search.search(self.database, "How does the C# using statement dispose an IDisposable?", 1)[0]
        self.assertTrue(hit.url.startswith("https://learn.microsoft.com/en-us/dotnet/csharp/language-reference/statements/using"))
        section = search.read(self.database, hit.id)
        self.assertIn("File.OpenText", section["body"])
        self.assertIn("Creative Commons Attribution 4.0", section["licence"])
        self.assertIn("MIT", section["licence"])

    def test_dotnet_and_csharp_name_the_family(self):
        self.assertEqual({"csharp"}, search.plan_for("C# records").families)
        self.assertEqual({"csharp"}, search.plan_for("in .NET how do I read a file").families)


class GitDocs(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.origin = root / "origin"
        for path, text in {**CSHARP_FILES, "docs/core/skipped.md": "# Not C#\n", "LICENSE": "Attribution 4.0 International\n",
                           "LICENSE-CODE": "The MIT License (MIT)\n", "docs/csharp/media/x.png": "png"}.items():
            target = self.origin / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        for command in (["init", "-q", "-b", "main"], ["add", "."], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "docs"]):
            subprocess.run(["git", *command], cwd=self.origin, check=True, capture_output=True)
        self.source = Source("csharp", "csharp", "", "dotnet", self.origin.as_uri(), "C# (.NET docs)")
        self.raw = root / "raw" / "csharp"
        self.raw.mkdir(parents=True)

    def tearDown(self):
        self.folder.cleanup()

    def test_only_the_csharp_docs_snippets_and_licences_are_kept_and_an_unchanged_repository_is_skipped(self):
        self.assertTrue(fetch.fetch_git_docs(self.source, self.raw))
        with tarfile.open(self.raw / fetch.GIT_ARCHIVE) as tar:
            names = sorted(tar.getnames())
        self.assertIn("docs/csharp/language-reference/statements/using.md", names)
        self.assertIn("samples/snippets/csharp/keywords/Legacy.cs", names)
        self.assertIn("LICENSE-CODE", names)
        self.assertNotIn("docs/core/skipped.md", names)
        self.assertFalse(any(name.endswith(".png") or name.startswith(".git") for name in names))
        self.assertEqual(40, len(fetch.read_meta(self.raw)["commit"]))
        self.assertFalse(fetch.fetch_git_docs(self.source, self.raw))
        self.assertEqual([], [path.name for path in self.raw.parent.iterdir() if path.name.startswith("doclib-git-")])

    def test_an_unreachable_repository_is_a_clear_error(self):
        broken = self.source._replace(location=(Path(self.folder.name) / "missing").as_uri())
        with self.assertRaises(fetch.FetchError) as raised:
            fetch.fetch_git_docs(broken, self.raw)
        self.assertIn("git ls-remote failed", str(raised.exception))


class CodingGate(unittest.TestCase):
    def test_coding_questions_pass_and_everyday_ones_do_not(self):
        for question in ("python str.removeprefix", "How do I read a file in Go?", "bash ${var:-x}", "fix TypeError: x"):
            with self.subTest(question=question):
                self.assertTrue(search.is_coding_question(question))
        for question in ("who won the world cup", "what is the weather tomorrow", "Go to the shops"):
            with self.subTest(question=question):
                self.assertFalse(search.is_coding_question(question))

    def test_versions_pick_the_matching_source(self):
        self.assertEqual(search.plan_for("Node 24 child_process spawn").sources, ["javascript", "dom", "node-24"])
        self.assertIn("python-3.13", search.plan_for("What's new in Python 3.13?").sources)
        self.assertNotIn("python-3.13", search.plan_for("python dataclasses").sources)

    def test_whats_new_questions_search_for_the_whats_new_pages(self):
        self.assertEqual(["what-s-new", "14"], search.plan_for("What's new in C# 14?").terms)
        self.assertIn("what-s-new", search.plan_for("What is new in Python 3.14").terms)
        self.assertNotIn("what-s-new", search.plan_for("create a new in-memory database in python").terms)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Service(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        root = Path(cls.folder.name)
        make_raw(root / "raw")
        cls.database = root / "docs.db"
        build.build(list(TEST_SOURCES), root / "raw", cls.database)
        cls.patch = unittest.mock.patch.object(search, "SOURCES", TEST_SOURCES)
        cls.patch.start()
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(cls.database))
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.http.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.patch.stop()
        cls.folder.cleanup()

    def get(self, path: str) -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_search_then_read(self):
        status, found = self.get("/search?q=Go+strings.Builder&limit=2")
        self.assertEqual(status, 200)
        first = found["results"][0]
        self.assertEqual(first["url"], "https://devdocs.io/go/strings/index#Builder")
        self.assertEqual(first["title"], first["heading"])
        status, section = self.get(f"/read?id={first['id']}")
        self.assertEqual(status, 200)
        self.assertIn("strings.Builder", section["text"])
        self.assertFalse(section["truncated"])

    def test_the_coding_gate_drops_everyday_questions(self):
        self.assertEqual(self.get("/search?q=who+won+the+world+cup&gate=coding")[1]["results"], [])
        self.assertTrue(self.get("/search?q=Go+strings.Builder+example&gate=coding")[1]["results"])

    def test_bad_requests_are_refused_clearly(self):
        self.assertEqual(self.get("/search")[0], 400)
        self.assertEqual(self.get("/search?q=" + "a" * 600)[0], 400)
        self.assertEqual(self.get("/read?id=abc")[0], 400)
        self.assertEqual(self.get("/read?id=999999")[0], 404)
        self.assertEqual(self.get("/nowhere")[0], 404)

    def test_a_section_id_below_one_is_refused_not_read_as_the_first(self):
        for bad in ("0", "-5"):
            with self.subTest(id=bad):
                status, answer = self.get(f"/read?id={bad}")
                self.assertEqual(status, 400)
                self.assertNotIn("text", answer)

    def test_only_a_missing_section_answers_404_and_other_lookup_errors_are_not_hidden(self):
        with self.assertRaises(server.NoSuchSection):
            server.read_answer(self.database, {"id": ["999999"]})
        self.assertFalse(issubclass(server.NoSuchSection, LookupError))
        with unittest.mock.patch.object(server, "read", side_effect=KeyError("body")), \
                self.assertRaises(KeyError):
            server.read_answer(self.database, {"id": ["1"]})

    def test_health_counts_the_library(self):
        status, health = self.get("/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["ok"])
        self.assertEqual(health["sources"], 4)

    def test_only_local_and_bridge_clients_are_served(self):
        bridge = (ipaddress.ip_network("172.17.0.0/16"),)
        self.assertTrue(server.is_allowed_client("127.0.0.1"))
        self.assertTrue(server.is_allowed_client("172.17.0.2", bridge))
        self.assertFalse(server.is_allowed_client("172.17.0.2"))
        self.assertFalse(server.is_allowed_client("192.168.1.50", bridge))
        self.assertFalse(server.is_allowed_client("not an address", bridge))

    def test_a_bridge_address_is_read_from_the_interface(self):
        shown = subprocess.CompletedProcess([], 0, json.dumps([{"addr_info": [{"local": "172.17.0.1", "prefixlen": 16}]}]), "")
        with unittest.mock.patch.object(subprocess, "run", return_value=shown):
            interface = server.bridge_interface("docker0")
        self.assertEqual(str(interface.ip), "172.17.0.1")
        self.assertEqual(str(interface.network), "172.17.0.0/16")
        missing = subprocess.CompletedProcess([], 1, "", 'Device "docker0" does not exist.')
        with unittest.mock.patch.object(subprocess, "run", return_value=missing), self.assertRaises(RuntimeError):
            server.bridge_interface("docker0")

    def test_a_missing_database_answers_503(self):
        handler = server.make_handler(Path(self.folder.name) / "missing.db")
        http = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        try:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(f"http://127.0.0.1:{http.server_address[1]}/health", timeout=5)
            self.assertEqual(raised.exception.code, 503)
        finally:
            http.shutdown()
            http.server_close()


class BrokenSource(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        make_raw(self.root / "raw")
        self.database = self.root / "docs.db"
        self.sources = unittest.mock.patch.object(refresh, "by_key", return_value=list(TEST_SOURCES))
        self.sources.start()

    def tearDown(self):
        self.sources.stop()
        self.folder.cleanup()

    def break_python_313(self):
        (self.root / "raw" / "python-3.13" / "docs-html.tar.bz2").write_bytes(b"not an archive")

    def indexed_sources(self) -> set[str]:
        with sqlite3.connect(self.database) as connection:
            return {row[0] for row in connection.execute("SELECT DISTINCT source FROM sections")}

    def refresh_quietly(self) -> int:
        with unittest.mock.patch("sys.stdout", new_callable=io.StringIO), self.assertLogs("doclib", "ERROR"):
            return refresh.main(["--no-fetch", "--root", str(self.root)])

    def test_a_source_that_fails_to_index_fails_the_refresh_and_keeps_the_old_index(self):
        build.build(list(TEST_SOURCES), self.root / "raw", self.database)
        self.break_python_313()
        self.assertEqual(self.refresh_quietly(), 1)
        self.assertIn("python-3.13", self.indexed_sources())
        self.assertFalse(self.database.with_name("docs.db.new").exists())

    def refresh_after_fetching(self, fetched: dict) -> tuple[int, dict]:
        everything = {source.key: "unchanged" for source in TEST_SOURCES}
        with unittest.mock.patch.object(refresh, "fetch_all", return_value={**everything, **fetched}), \
                unittest.mock.patch("sys.stdout", new_callable=io.StringIO) as printed:
            code = refresh.main(["--root", str(self.root), "--reserve-gb", "0"])
        return code, json.loads(printed.getvalue())

    def test_a_failed_build_is_tried_again_even_when_nothing_new_was_fetched(self):
        build.build(list(TEST_SOURCES), self.root / "raw", self.database)
        good_download = (self.root / "raw" / "python-3.13" / "docs-html.tar.bz2").read_bytes()
        self.break_python_313()
        with self.assertLogs("doclib", "ERROR"):
            self.assertEqual(1, self.refresh_after_fetching({"python-3.13": "updated"})[0])
        with self.assertLogs("doclib", "ERROR"):
            code, report = self.refresh_after_fetching({})
        self.assertEqual(1, code)
        self.assertIn("couldn't be indexed", report["sections"])
        (self.root / "raw" / "python-3.13" / "docs-html.tar.bz2").write_bytes(good_download)
        code, report = self.refresh_after_fetching({})
        self.assertEqual(0, code)
        self.assertIsInstance(report["sections"], dict)
        code, report = self.refresh_after_fetching({})
        self.assertEqual((0, "unchanged; index kept"), (code, report["sections"]))

    def test_a_source_that_cannot_be_indexed_is_downloaded_again_next_time(self):
        folder = self.root / "raw" / "python-3.13"
        fetch.write_meta(folder, {"url": "https://example.org/docs.tar.bz2", "etag": '"v1"', "last_modified": "yesterday",
                                  "release": "3.13.1"})
        build.build(list(TEST_SOURCES), self.root / "raw", self.database)
        self.assertEqual('"v1"', fetch.read_meta(folder)["etag"])
        self.break_python_313()
        with self.assertLogs("doclib", "ERROR"):
            self.assertEqual(1, self.refresh_after_fetching({"python-3.13": "updated"})[0])
        self.assertEqual({"url": "https://example.org/docs.tar.bz2", "release": "3.13.1"}, fetch.read_meta(folder))
        self.assertEqual({"User-agent": fetch.USER_AGENT},
                         dict(fetch.request_for("https://example.org/docs.tar.bz2", fetch.read_meta(folder)).header_items()))

    def test_forgetting_the_saved_version_keeps_the_release_attribution_and_links(self):
        folder = self.root / "raw" / "kept-meta"
        folder.mkdir(parents=True, exist_ok=True)
        described = {"release": "1.27", "attribution": "&copy; Google, Inc.", "links": {"home": "https://go.dev/"}}
        for version in ({"mtime": 1700000000}, {"commit": "abc123"}, {"file": "debian-reference_2.0_all.deb"},
                        {"etag": '"v1"', "last_modified": "yesterday"}):
            with self.subTest(version=version):
                fetch.write_meta(folder, {**described, **version})
                fetch.forget_saved_version(folder)
                self.assertEqual(described, fetch.read_meta(folder))

    def test_a_package_whose_version_was_forgotten_is_fetched_again(self):
        folder = self.root / "raw" / "debian-ref"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "debian-reference_2.0_all.deb").write_bytes(b"old")

        def fake_download(arguments, cwd, **_):
            (Path(cwd) / "debian-reference_2.0_all.deb").write_bytes(b"new")
            return subprocess.CompletedProcess(arguments, 0, "", "")

        with unittest.mock.patch.object(fetch.subprocess, "run", side_effect=fake_download):
            self.assertTrue(fetch.fetch_deb("debian-reference", folder))
            self.assertFalse(fetch.fetch_deb("debian-reference", folder))
        self.assertEqual(b"new", (folder / "debian-reference_2.0_all.deb").read_bytes())

    def test_without_an_old_index_the_rest_is_still_indexed_and_the_refresh_still_fails(self):
        self.break_python_313()
        self.assertEqual(self.refresh_quietly(), 1)
        self.assertEqual(self.indexed_sources(), {"python-3.14", "peps", "go"})


class FakeOrigin(BaseHTTPRequestHandler):
    body = b"payload"
    etag = '"v1"'

    def do_GET(self):
        if self.headers.get("If-None-Match") == FakeOrigin.etag:
            self.send_response(304)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("ETag", FakeOrigin.etag)
        self.send_header("Content-Length", str(len(FakeOrigin.body)))
        self.end_headers()
        self.wfile.write(FakeOrigin.body)

    def log_message(self, format, *args):
        pass


class Downloads(unittest.TestCase):
    def setUp(self):
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), FakeOrigin)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.http.server_address[1]}/file"
        self.folder = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.folder.cleanup()

    def test_an_unchanged_file_is_not_downloaded_again(self):
        folder = Path(self.folder.name)
        self.assertTrue(fetch.fetch_url(self.url, folder, "page.html"))
        self.assertEqual((folder / "page.html").read_bytes(), b"payload")
        self.assertFalse(fetch.fetch_url(self.url, folder, "page.html"))

    def test_an_over_large_download_is_refused_and_not_kept(self):
        target = Path(self.folder.name) / "big"
        with unittest.mock.patch.object(fetch, "MAX_DOWNLOAD_BYTES", 3), unittest.mock.patch.object(fetch, "CHUNK_BYTES", 2):
            with self.assertRaises(fetch.FetchError):
                fetch.download(self.url, target)
        self.assertFalse(target.exists())
        self.assertFalse(target.with_name("big.part").exists())

    def test_an_unreachable_origin_is_a_clear_error(self):
        with self.assertRaises(fetch.FetchError) as raised:
            fetch.download(f"http://127.0.0.1:{free_port()}/x", Path(self.folder.name) / "x")
        self.assertIn("couldn't download", str(raised.exception))


if __name__ == "__main__":
    unittest.main()

import assert from "node:assert/strict";
import { test } from "node:test";
import { renderMarkdown, speakableText } from "../js/markdown.js";
import { artyFilePath, fileNameFromPath, mediaKind } from "../js/links.js";

test("html in a reply is shown as text, never run", () => {
  const html = renderMarkdown("<script>alert(1)</script> <img src=x onerror=alert(1)>");
  assert.equal(html.includes("<script>"), false);
  assert.equal(html.includes("<img"), false);
  assert.match(html, /&lt;script&gt;/);
});

test("only web and Arty file links become links", () => {
  assert.equal(renderMarkdown("[click](javascript:alert(1))").includes("<a"), false);
  assert.equal(renderMarkdown("[x](data:text/html,hi)").includes("<a"), false);
  const quoted = renderMarkdown("[x](https://a.example/\"onmouseover=\"alert(1))");
  assert.equal(quoted.includes("\" onmouseover"), false);
  assert.match(renderMarkdown("[docs](https://example.com/a_b_c)"),
    /<a href="https:\/\/example.com\/a_b_c" target="_blank" rel="noopener noreferrer">docs<\/a>/);
});

test("Arty's files open through the app and pictures load through it", () => {
  assert.match(renderMarkdown("[report.pdf](http://192.0.2.13:8443/arty-files/ab/tok/report.pdf)"),
    /<a href="#" class="arty-file" data-path="\/arty-files\/ab\/tok\/report.pdf">report.pdf<\/a>/);
  assert.match(renderMarkdown("![chart](http://192.0.2.13:8443/arty-files/ab/tok/chart.png)"),
    /<img class="arty-file-image" data-path="\/arty-files\/ab\/tok\/chart.png" alt="chart">/);
  assert.equal(renderMarkdown("![x](https://tracker.example/pixel.png)").includes("<img"), false);
});

test("bare web addresses become links without the full stop after them", () => {
  assert.equal(renderMarkdown("See https://example.com/page."),
    "<p>See <a href=\"https://example.com/page\" target=\"_blank\" rel=\"noopener noreferrer\">https://example.com/page</a>.</p>");
});

test("common markdown renders", () => {
  assert.equal(renderMarkdown("## Plan\n- **one**\n- *two*\n\n1. a\n2. b"),
    "<h4>Plan</h4><ul><li><strong>one</strong></li><li><em>two</em></li></ul><ol><li>a</li><li>b</li></ol>");
  assert.equal(renderMarkdown("```\nif a < b:\n  print(1)\n```"), "<pre><code>if a &lt; b:\n  print(1)</code></pre>");
  assert.equal(renderMarkdown("use `rm -rf *` carefully"), "<p>use <code>rm -rf *</code> carefully</p>");
  assert.equal(renderMarkdown("> Bins tonight\n\nline one\nline two"), "<blockquote>Bins tonight</blockquote><p>line one<br>line two</p>");
  assert.equal(renderMarkdown("snake_case_name stays"), "<p>snake_case_name stays</p>");
  assert.equal(renderMarkdown(""), "");
  assert.equal(renderMarkdown(null), "");
});

test("speech skips quotes, code and link addresses", () => {
  assert.equal(speakableText("> quoted\n**Done.** See [the report](https://x.example).\n```\ncode\n```"), "Done. See the report.\n (code)");
});

test("Arty file links and media kinds are recognised", () => {
  assert.equal(artyFilePath("http://192.0.2.13:8443/arty-files/a/b/c.mp4"), "/arty-files/a/b/c.mp4");
  assert.equal(artyFilePath("https://example.com/c.mp4"), null);
  assert.equal(artyFilePath("/arty-files/a/b/c.png?size=2"), "/arty-files/a/b/c.png");
});

test("only the brain's own file link shape counts as an Arty file", () => {
  for (const link of ["/arty-files/../v1/memory", "/arty-files/%2e%2e/v1/x/y", "/arty-files/a/../../v1/models",
    "https://x.example/arty-files/a/b", "/arty-files/a/b/"]) {
    assert.equal(artyFilePath(link), null, link);
  }
  assert.equal(renderMarkdown("![x](/arty-files/../v1/memory)"), "<p>![x](/arty-files/../v1/memory)</p>");
  assert.equal(fileNameFromPath("/arty-files/a/b/my%20notes.pdf"), "my notes.pdf");
  assert.equal(mediaKind("/arty-files/a/b/clip.MOV"), "video");
  assert.equal(mediaKind("/arty-files/a/b/photo.heic?x=1"), "picture");
  assert.equal(mediaKind("/arty-files/a/b/notes.pdf"), null);
  assert.equal(mediaKind("/arty-files/a/b/notes", true), "picture");
});

test("a bare link inside quotes stops before the closing quote", () => {
  assert.equal(renderMarkdown("Open \"https://example.com/a\" now"),
    "<p>Open &quot;<a href=\"https://example.com/a\" target=\"_blank\" rel=\"noopener noreferrer\">https://example.com/a</a>&quot; now</p>");
  assert.equal(renderMarkdown("'https://example.com/b'"),
    "<p>&#39;<a href=\"https://example.com/b\" target=\"_blank\" rel=\"noopener noreferrer\">https://example.com/b</a>&#39;</p>");
  assert.match(renderMarkdown("https://example.com/?a=1&b=2"), /href="https:\/\/example.com\/\?a=1&amp;b=2"/);
});

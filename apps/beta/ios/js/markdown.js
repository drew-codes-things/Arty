import { artyFilePath } from "./links.js";

const TOKEN_MARK = "\u0000";
const TOKEN_PATTERN = /\u0000(\d+)\u0000/g;
const FENCE = /^\s*```/;
const HEADING = /^(#{1,6})\s+(.*)$/;
const QUOTE = /^\s*&gt;\s?(.*)$/;
const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;
const TRAILING_PUNCTUATION = /[.,;:!?)]+$/;
const WEB_LINK = /^https?:[/][/]/i;

export function escapeHtml(text) {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function unescapeHtml(text) {
  return text.replace(/&#39;/g, "'").replace(/&quot;/g, "\"").replace(/&gt;/g, ">").replace(/&lt;/g, "<").replace(/&amp;/g, "&");
}

function linkHtml(label, escapedUrl) {
  const url = unescapeHtml(escapedUrl);
  const filePath = artyFilePath(url);
  if (filePath) return `<a href="#" class="arty-file" data-path="${escapeHtml(filePath)}">${label}</a>`;
  if (WEB_LINK.test(url)) return `<a href="${escapedUrl}" target="_blank" rel="noopener noreferrer">${label}</a>`;
  return null;
}

function imageHtml(alt, escapedUrl) {
  const filePath = artyFilePath(unescapeHtml(escapedUrl));
  if (filePath) return `<img class="arty-file-image" data-path="${escapeHtml(filePath)}" alt="${alt}">`;
  return linkHtml(alt || escapedUrl, escapedUrl);
}

export function renderInline(escaped) {
  const tokens = [];
  const keep = (html) => `${TOKEN_MARK}${tokens.push(html) - 1}${TOKEN_MARK}`;
  let text = escaped.replace(/`([^`]+)`/g, (_, code) => keep(`<code>${code}</code>`));
  text = text.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (whole, alt, url) => {
    const html = imageHtml(alt, url);
    return html ? keep(html) : whole;
  });
  text = text.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (whole, label, url) => {
    const html = linkHtml(label, url);
    return html ? keep(html) : whole;
  });
  text = text.replace(/https?:[/][/][^\s<>"]+/gi, (url) => {
    const trailing = url.match(TRAILING_PUNCTUATION)?.[0] ?? "";
    const bare = url.slice(0, url.length - trailing.length);
    return keep(linkHtml(bare, bare)) + trailing;
  });
  text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>").replace(/__([^_]+)__/g, "<strong>$1</strong>");
  text = text.replace(/(^|[^\w*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>").replace(/(^|[^\w_])_([^_\s][^_]*)_(?!\w)/g, "$1<em>$2</em>");
  return text.replace(TOKEN_PATTERN, (_, index) => tokens[Number(index)]);
}

function listHtml(tag, items) {
  return `<${tag}>${items.map((item) => `<li>${renderInline(item)}</li>`).join("")}</${tag}>`;
}

export function renderMarkdown(markdown) {
  const lines = escapeHtml(String(markdown ?? "").replace(/\u0000/g, "")).split(/\r?\n/);
  const blocks = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (FENCE.test(line)) {
      const code = [];
      index += 1;
      while (index < lines.length && !FENCE.test(lines[index])) code.push(lines[index++]);
      index += 1;
      blocks.push(`<pre><code>${code.join("\n")}</code></pre>`);
      continue;
    }
    const heading = line.match(HEADING);
    if (heading) {
      const level = Math.min(heading[1].length + 2, 6);
      blocks.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      index += 1;
      continue;
    }
    const grouped = collectGroup(lines, index);
    if (grouped) {
      blocks.push(grouped.html);
      index = grouped.next;
      continue;
    }
    if (!line.trim()) {
      index += 1;
      continue;
    }
    const paragraph = [];
    while (index < lines.length && lines[index].trim() && !startsBlock(lines[index])) paragraph.push(lines[index++]);
    blocks.push(`<p>${paragraph.map(renderInline).join("<br>")}</p>`);
  }
  return blocks.join("");
}

function startsBlock(line) {
  return FENCE.test(line) || HEADING.test(line) || QUOTE.test(line) || BULLET.test(line) || NUMBERED.test(line);
}

function collectGroup(lines, start) {
  for (const [pattern, wrap] of [
    [QUOTE, (items) => `<blockquote>${items.map(renderInline).join("<br>")}</blockquote>`],
    [BULLET, (items) => listHtml("ul", items)],
    [NUMBERED, (items) => listHtml("ol", items)],
  ]) {
    if (!pattern.test(lines[start])) continue;
    const items = [];
    let next = start;
    while (next < lines.length && pattern.test(lines[next])) items.push(lines[next++].match(pattern)[1]);
    return { html: wrap(items), next };
  }
  return null;
}

export function speakableText(markdown) {
  return String(markdown ?? "")
    .replace(/^\s*>.*$\n?/gm, "")
    .replace(/```[\s\S]*?```/g, " (code) ")
    .replace(/!\[[^\]]*]\([^)]+\)/g, "")
    .replace(/\[([^\]]+)]\([^)]+\)/g, "$1")
    .replace(/[*`#_>]/g, "")
    .replace(/^\s*[-•]\s+/gm, "")
    .trim();
}

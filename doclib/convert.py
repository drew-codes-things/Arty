import re
from html.parser import HTMLParser
from typing import NamedTuple

SKIPPED_TAGS = {"script", "style", "nav", "noscript", "svg", "button", "form", "select", "iframe", "template"}
SKIPPED_CLASSES = {"headerlink", "sphinxsidebar", "related", "footer", "breadcrumbs", "navheader", "navfooter", "toc",
                   "copybutton", "rustdoc-breadcrumbs", "src", "anchor", "sidebar", "_attribution", "method-source-code",
                   "method-click-advice", "rightside"}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
BLOCK_TAGS = {"p", "div", "section", "article", "ul", "ol", "dl", "dd", "table", "tr", "blockquote", "figure",
              "figcaption", "details", "summary", "aside", "main", "header", "footer", "thead", "tbody", "caption"}
INLINE_CODE_TAGS = {"code", "tt", "kbd", "samp"}
HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
API_ENTRY_CLASSES = {"function", "method", "class", "attribute", "data", "exception", "decorator", "describe", "option",
                     "envvar", "property", "type", "macro", "member", "var", "classmethod", "staticmethod", "opcode"}
API_HEADING_LEVEL = 4
SECTION_LEVELS = 4
SECTION_CHARS = 4000
HEADING_CHARS = 200
CODE_LANGUAGE_CLASS = re.compile(r"(?:language|lang|highlight|brush)-(?:source-)?([a-z0-9+#]+)", re.IGNORECASE)
GENERIC_CODE_LANGUAGES = {"default", "none", "text", "plain", "console", "nohighlight"}
SPACES = re.compile(r"[ \t\r\f\v\n]+")
BLANK_LINES = re.compile(r"\n{3,}")
SPACE_BEFORE_NEWLINE = re.compile(r"[ \t]+\n")
FENCE = "```"


class Event(NamedTuple):
    kind: str
    level: int
    text: str
    anchor: str


class Section(NamedTuple):
    page_title: str
    heading: str
    anchor: str
    body: str


def main_marker(html: str) -> str:
    if 'role="main"' in html:
        return "role-main"
    if "<main" in html:
        return "main"
    if 'id="content"' in html:
        return "content"
    return ""


def starts_main(tag: str, attributes: dict, marker: str) -> bool:
    if marker == "role-main":
        return attributes.get("role") == "main"
    if marker == "main":
        return tag == "main"
    if marker == "content":
        return attributes.get("id") == "content"
    return False


def class_names(attributes: dict) -> set[str]:
    return set((attributes.get("class") or "").split())


def is_skipped(tag: str, attributes: dict) -> bool:
    if tag in SKIPPED_TAGS:
        return True
    if attributes.get("aria-hidden") == "true" and tag not in BLOCK_TAGS:
        return True
    return bool(class_names(attributes) & SKIPPED_CLASSES)


def code_language(attributes: dict, ancestors_classes: list[set[str]]) -> str:
    if attributes.get("data-language"):
        return attributes["data-language"].lower()
    for classes in [class_names(attributes)] + ancestors_classes[::-1]:
        for name in classes:
            if found := CODE_LANGUAGE_CLASS.match(name):
                language = found.group(1).lower()
                return "" if language in GENERIC_CODE_LANGUAGES else language
    return ""


def is_api_entry(tag: str, attributes: dict, parent_classes: set[str]) -> bool:
    classes = class_names(attributes)
    if tag == "dt":
        return bool(attributes.get("id")) and bool(parent_classes & API_ENTRY_CLASSES)
    if "code-header" in classes:
        return True
    return "method-heading" in classes and bool(attributes.get("id"))


class PageParser(HTMLParser):
    def __init__(self, marker: str):
        super().__init__(convert_charrefs=True)
        self.marker = marker
        self.inside_main = not marker
        self.main_depth = 0
        self.stack: list[tuple[str, set[str]]] = []
        self.skip_depth = 0
        self.events: list[Event] = []
        self.text: list[str] = []
        self.heading: list[str] | None = None
        self.heading_level = 0
        self.heading_anchor = ""
        self.heading_depth = 0
        self.pending_anchor = ""
        self.code: list[str] | None = None
        self.code_language = ""
        self.list_depth = 0

    def flush_text(self):
        joined = "".join(self.text)
        self.text.clear()
        if joined.strip():
            self.events.append(Event("text", 0, joined, ""))

    def newline(self):
        self.text.append("\n")

    def handle_starttag(self, tag, attrs):
        attributes = {name: value or "" for name, value in attrs}
        if tag in VOID_TAGS:
            if tag == "br" and self.inside_main and not self.skip_depth:
                (self.code if self.code is not None else self.text).append("\n")
            return
        parent_classes = self.stack[-1][1] if self.stack else set()
        self.stack.append((tag, class_names(attributes)))
        if not self.inside_main:
            if starts_main(tag, attributes, self.marker):
                self.inside_main = True
                self.main_depth = len(self.stack)
            return
        if self.skip_depth or is_skipped(tag, attributes):
            self.skip_depth = self.skip_depth or len(self.stack)
            return
        if attributes.get("id") and tag not in HEADING_TAGS:
            self.pending_anchor = attributes["id"]
        if self.code is not None:
            return
        if self.heading is None and (tag in HEADING_TAGS or is_api_entry(tag, attributes, parent_classes)):
            self.flush_text()
            self.heading = []
            self.heading_depth = len(self.stack)
            self.heading_level = HEADING_TAGS.get(tag, API_HEADING_LEVEL)
            self.heading_anchor = attributes.get("id") or self.pending_anchor
        elif tag == "pre" and self.heading is None:
            self.flush_text()
            self.code = []
            self.code_language = code_language(attributes, [classes for _, classes in self.stack[:-1]])
        elif tag in INLINE_CODE_TAGS and self.heading is None:
            self.text.append("`")
        elif tag == "li":
            self.newline()
            self.text.append("  " * max(self.list_depth - 1, 0) + "- ")
        elif tag in ("ul", "ol"):
            self.list_depth += 1
            self.newline()
        elif tag in ("td", "th"):
            self.text.append(" | ")
        elif tag == "dt" or tag in BLOCK_TAGS:
            self.newline()

    def handle_endtag(self, tag):
        if tag in VOID_TAGS or not any(open_tag == tag for open_tag, _ in self.stack):
            return
        while self.stack[-1][0] != tag:
            self.close_top(len(self.stack))
        self.close_top(len(self.stack))

    def close_top(self, depth: int):
        tag, _ = self.stack.pop()
        if self.main_depth and depth == self.main_depth:
            self.flush_text()
            self.inside_main = False
            self.main_depth = 0
            return
        if not self.inside_main:
            return
        if self.skip_depth:
            if depth <= self.skip_depth:
                self.skip_depth = 0
            return
        if tag == "pre" and self.code is not None:
            body = "".join(self.code).strip("\n")
            if body.strip():
                self.events.append(Event("code", 0, f"{FENCE}{self.code_language}\n{body}\n{FENCE}", ""))
            self.code = None
            return
        if self.code is not None:
            return
        if self.heading is not None and depth == self.heading_depth:
            text = SPACES.sub(" ", "".join(self.heading)).strip()[:HEADING_CHARS]
            if text:
                self.events.append(Event("heading", self.heading_level, text, self.heading_anchor))
            self.heading = None
            self.pending_anchor = ""
            return
        if tag in INLINE_CODE_TAGS and self.heading is None:
            self.text.append("`")
        elif tag in ("ul", "ol"):
            self.list_depth = max(self.list_depth - 1, 0)
            self.newline()
        elif tag == "dt" or tag in BLOCK_TAGS or tag == "li":
            self.newline()

    def handle_data(self, data):
        if not self.inside_main or self.skip_depth:
            return
        if self.code is not None:
            self.code.append(data)
        elif self.heading is not None:
            self.heading.append(data)
        else:
            self.text.append(SPACES.sub(" ", data))

    def close(self):
        super().close()
        while self.stack:
            self.close_top(len(self.stack))
        self.flush_text()


def tidy(text: str) -> str:
    lines = [line.strip() if not line.lstrip().startswith("- ") else line.rstrip() for line in text.split("\n")]
    joined = SPACE_BEFORE_NEWLINE.sub("\n", "\n".join(lines))
    return BLANK_LINES.sub("\n\n", joined.replace("``", "")).strip()


def html_events(html: str) -> list[Event]:
    parser = PageParser(main_marker(html))
    parser.feed(html)
    parser.close()
    return [Event(event.kind, event.level, tidy(event.text), event.anchor) if event.kind == "text" else event
            for event in parser.events]


def chunks_of(parts: list[str], limit: int = SECTION_CHARS) -> list[str]:
    chunks, current, used = [], [], 0
    for part in parts:
        if current and used + len(part) > limit:
            chunks.append("\n\n".join(current))
            current, used = [], 0
        current.append(part)
        used += len(part) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def sections_from_events(events: list[Event], page_title: str = "") -> list[Section]:
    title = page_title or next((event.text for event in events if event.kind == "heading"), "")
    sections, path, anchor, parts = [], [], "", []

    def close():
        body_parts = [part for part in parts if part.strip()]
        if body_parts:
            heading = " > ".join(dict.fromkeys([title] + [name for _, name in path]))
            pieces = chunks_of(body_parts)
            for number, piece in enumerate(pieces, start=1):
                shown = heading if len(pieces) == 1 else f"{heading} (part {number})"
                sections.append(Section(title, shown, anchor, piece))
        parts.clear()

    for event in events:
        if event.kind == "heading" and event.level <= SECTION_LEVELS:
            close()
            path = [(level, name) for level, name in path if level < event.level] + [(event.level, event.text)]
            anchor = event.anchor
        elif event.kind == "heading":
            parts.append(f"**{event.text}**")
        elif event.kind == "text":
            parts.extend(event.text.split("\n\n"))
        else:
            parts.append(event.text)
    close()
    return sections


def html_sections(html: str, page_title: str = "") -> list[Section]:
    return sections_from_events(html_events(html), page_title)


RST_ADORNMENT = re.compile(r"^([=\-~^\"'*+#<>:._`])\1{2,}\s*$")
RST_FIELD = re.compile(r"^([A-Za-z-]+):\s*(.*)$")
RST_CODE_DIRECTIVE = re.compile(r"^(\s*)\.\.\s+(?:code-block|code|sourcecode)::\s*(\S*)\s*$")
RST_OTHER_DIRECTIVE = re.compile(r"^(\s*)\.\.\s+[\w:-]+::.*$")
RST_TARGET_OR_COMMENT = re.compile(r"^\s*\.\.(?:\s+_[^:]*:.*|\s+\[[^\]]+\].*|\s*)$")
RST_OPTION = re.compile(r"^\s*:[\w-]+:.*$")
RST_ROLE_WITH_TARGET = re.compile(r":[\w:-]+:`([^`<]+?)\s*<[^>]*>`")
RST_ROLE = re.compile(r":[\w:-]+:`([^`]+)`")
RST_LINK = re.compile(r"`([^`<]+?)\s*<[^>]*>`_{1,2}")
RST_REFERENCE = re.compile(r"`([^`]+)`_{1,2}")
RST_LITERAL = re.compile(r"``(.+?)``")
ANCHOR_CHARACTERS = re.compile(r"[^a-z0-9]+")


def rst_anchor(title: str) -> str:
    return ANCHOR_CHARACTERS.sub("-", title.lower()).strip("-")


def rst_inline(text: str) -> str:
    text = RST_ROLE_WITH_TARGET.sub(r"\1", text)
    text = RST_ROLE.sub(lambda found: found.group(1).lstrip("!~"), text)
    text = RST_LINK.sub(r"\1", text)
    text = RST_REFERENCE.sub(r"\1", text)
    return RST_LITERAL.sub(r"`\1`", text)


def indentation(line: str) -> int:
    return len(line) - len(line.lstrip())


def indented_block(lines: list[str], start: int, outer: int) -> tuple[list[str], int]:
    end = start
    while end < len(lines) and (not lines[end].strip() or indentation(lines[end]) > outer):
        end += 1
    block = lines[start:end]
    while block and not block[-1].strip():
        block.pop()
    while block and (not block[0].strip() or RST_OPTION.match(block[0])):
        block.pop(0)
    inner = min((indentation(line) for line in block if line.strip()), default=0)
    return [line[inner:] for line in block], end


def rst_fields(lines: list[str]) -> tuple[dict[str, str], int]:
    fields, index, last = {}, 0, ""
    while index < len(lines) and lines[index].strip():
        line = lines[index]
        found = RST_FIELD.match(line)
        if found and not line[0].isspace():
            last = found.group(1)
            fields[last] = found.group(2).strip()
        elif last:
            fields[last] += " " + line.strip()
        index += 1
    return fields, index


def is_title_at(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    text, under = lines[index], lines[index + 1]
    return (bool(text.strip()) and not text[0].isspace() and RST_ADORNMENT.match(under) is not None
            and len(under.rstrip()) >= len(text.rstrip()) - 1 and not RST_ADORNMENT.match(text))


def rst_events(text: str) -> tuple[dict[str, str], list[Event]]:
    lines = text.splitlines()
    fields, index = rst_fields(lines)
    events, styles, paragraph = [], [], []

    def flush():
        if paragraph:
            events.append(Event("text", 0, rst_inline(" ".join(part.strip() for part in paragraph)), ""))
            paragraph.clear()

    literal_next, literal_indent = False, 0
    while index < len(lines):
        line = lines[index]
        if RST_ADORNMENT.match(line) and is_title_at(lines, index + 1):
            index += 1
            continue
        if is_title_at(lines, index):
            flush()
            style = lines[index + 1].strip()[0]
            if style not in styles:
                styles.append(style)
            title = line.strip()
            events.append(Event("heading", styles.index(style) + 2, rst_inline(title), rst_anchor(title)))
            index += 2
            continue
        code = RST_CODE_DIRECTIVE.match(line)
        if code or (literal_next and line.strip() and indentation(line) > literal_indent):
            flush()
            outer = len(code.group(1)) if code else literal_indent
            block, index = indented_block(lines, index + 1 if code else index, outer)
            language = code.group(2) if code else ""
            if block:
                events.append(Event("code", 0, f"{FENCE}{language}\n" + "\n".join(block) + f"\n{FENCE}", ""))
            literal_next = False
            continue
        if RST_TARGET_OR_COMMENT.match(line) or RST_OTHER_DIRECTIVE.match(line):
            flush()
            index += 1
            continue
        if not line.strip():
            flush()
            index += 1
            continue
        stripped = line.rstrip()
        if stripped.endswith("::"):
            literal_next = True
            literal_indent = indentation(line)
            stripped = stripped[:-2].rstrip() + (":" if stripped.strip() != "::" else "")
            if stripped.strip():
                paragraph.append(stripped)
            flush()
            index += 1
            continue
        literal_next = False
        paragraph.append(stripped)
        index += 1
    flush()
    return fields, events


def rst_document(text: str) -> tuple[dict[str, str], list[Section]]:
    fields, events = rst_events(text)
    return fields, sections_from_events(events, fields.get("Title", ""))

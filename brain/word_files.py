from dataclasses import dataclass

import xml_stream
from xml_stream import FileReadError, StopReading

MAX_TEXT_CHARS = 2_000_000
WORD_FORMATS = {"docx", "docm", "odt"}
OLD_WORD_FORMATS = {"doc", "rtf", "wps"}
CELL_SEPARATOR = " | "
DOCX_PART = "word/document.xml"
ODT_PART = "content.xml"
DOCX_PARAGRAPHS = {"p"}
ODT_PARAGRAPHS = {"p", "h"}
DOCX_CELLS, ODT_CELLS = {"tc"}, {"table-cell", "covered-table-cell"}
DOCX_ROWS, ODT_ROWS = {"tr"}, {"table-row"}
BREAK_TAGS = {"br", "line-break"}


@dataclass
class WordText:
    text: str
    cut_off: bool


class Collector:
    def __init__(self, paragraphs, cells, rows, text_tag, skip_tag):
        self.paragraphs, self.cells, self.rows = paragraphs, cells, rows
        self.text_tag, self.skip_tag = text_tag, skip_tag
        self.lines: list[str] = []
        self.size = 0
        self.paragraph: list[str] | None = None
        self.depth_in_paragraph = 0
        self.in_text = 0
        self.skipping = 0
        self.cell_stack: list[list[str]] = []
        self.row_stack: list[list[str]] = []

    def start(self, tag, attributes):
        if tag == self.skip_tag:
            self.skipping += 1
        elif tag in self.paragraphs and not self.skipping:
            self.depth_in_paragraph += 1
            if self.depth_in_paragraph == 1:
                self.paragraph = []
        elif tag in self.cells:
            self.cell_stack.append([])
        elif tag in self.rows:
            self.row_stack.append([])
        elif tag == self.text_tag:
            self.in_text += 1
        elif self.paragraph is not None and not self.skipping:
            self.add_special(tag, attributes)

    def add_special(self, tag, attributes):
        if tag == "tab":
            self.paragraph.append("\t")
        elif tag in BREAK_TAGS:
            self.paragraph.append("\n")
        elif tag == "s":
            count = attributes.get("c", "1")
            self.paragraph.append(" " * (int(count) if count.isdigit() else 1))

    def text(self, data):
        if self.paragraph is None or self.skipping:
            return
        if self.text_tag is None or self.in_text:
            self.paragraph.append(data)

    def end(self, tag):
        if tag == self.skip_tag:
            self.skipping -= 1
        elif tag == self.text_tag:
            self.in_text -= 1
        elif tag in self.paragraphs and not self.skipping and self.depth_in_paragraph:
            self.depth_in_paragraph -= 1
            if self.depth_in_paragraph == 0:
                self.finish_paragraph()
        elif tag in self.cells and self.cell_stack:
            cell = " ".join(part for part in self.cell_stack.pop() if part)
            if self.row_stack:
                self.row_stack[-1].append(cell)
        elif tag in self.rows and self.row_stack:
            row = self.row_stack.pop()
            if any(row):
                self.emit(CELL_SEPARATOR.join(row))

    def finish_paragraph(self):
        text = "".join(self.paragraph).strip()
        self.paragraph = None
        if self.cell_stack:
            self.cell_stack[-1].append(" ".join(text.split()))
        elif text:
            self.emit(text)

    def emit(self, line: str):
        self.lines.append(line)
        self.size += len(line) + 1
        if self.size > MAX_TEXT_CHARS:
            raise StopReading()


def read_word_file(path: str, name: str) -> WordText:
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if extension in OLD_WORD_FORMATS:
        raise FileReadError(f"{name} is an older document format; ask for it to be saved as .docx, .odt or PDF")
    if extension not in WORD_FORMATS:
        raise FileReadError(f"{name} isn't a Word or OpenDocument text file")
    is_docx = extension in ("docx", "docm")
    collector = Collector(DOCX_PARAGRAPHS if is_docx else ODT_PARAGRAPHS, DOCX_CELLS if is_docx else ODT_CELLS,
                          DOCX_ROWS if is_docx else ODT_ROWS, "t" if is_docx else None, "pPr" if is_docx else "annotation")
    archive = xml_stream.open_archive(path, "Word (.docx)" if is_docx else "OpenDocument text (.odt)")
    with archive:
        finished = xml_stream.walk(archive, DOCX_PART if is_docx else ODT_PART, collector.start, collector.end, collector.text)
    return WordText("\n".join(collector.lines), not finished)


def is_word_file(name: str) -> bool:
    extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return extension in WORD_FORMATS | OLD_WORD_FORMATS

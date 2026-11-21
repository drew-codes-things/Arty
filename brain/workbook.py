import csv
import io
import posixpath
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import xml_stream
from xml_stream import FileReadError, StopReading

MAX_CELLS = 1_500_000
MAX_COLUMNS = 2000
MAX_FORMULAS = 20_000
MAX_ROW_REPEAT = 1000
MAX_CELL_REPEAT = 1000
CSV_SNIFF_CHARS = 4096
CSV_DELIMITERS = ",;\t|"
CSV_FIELD_LIMIT = 1 << 20
CSV_ENCODING_SAMPLE_BYTES = 1 << 20
UTF8_CHARACTER_BYTES = 4
SPREADSHEET_FORMATS = {"xlsx", "xlsm", "ods", "csv", "tsv"}
OLD_EXCEL_FORMATS = {"xls", "xlsb"}
EXCEL_EPOCH = datetime(1899, 12, 30)
EXCEL_EPOCH_1904 = datetime(1904, 1, 1)
LAST_EXCEL_SERIAL = 2958465
BUILTIN_DATE_FORMATS = set(range(14, 23)) | set(range(27, 37)) | set(range(45, 48)) | set(range(50, 59))
QUOTED_OR_BRACKETED = re.compile(r'"[^"]*"|\[[^\]]*\]|\\.')
DATE_LETTERS = re.compile(r"[ymdhs]", re.IGNORECASE)
CELL_REFERENCE = re.compile(r"^([A-Z]+)(\d+)$")
ODS_NUMBER_TYPES = {"float", "percentage", "currency"}
ODS_FORMULA_PREFIX = re.compile(r"^of:")


class ErrorValue(str):
    pass


@dataclass
class Sheet:
    name: str
    rows: list[list] = field(default_factory=list)
    row_numbers: list[int] = field(default_factory=list)
    formulas: dict[tuple[int, int], str] = field(default_factory=dict)
    hidden: bool = False
    cut_off: str = ""

    def width(self) -> int:
        return max((len(row) for row in self.rows), default=0)


@dataclass
class Workbook:
    sheets: list[Sheet]
    kind: str


class CellBudget:
    def __init__(self, limit: int | None = None):
        self.left = MAX_CELLS if limit is None else limit
        self.spent_out = False

    def spend(self, amount: int) -> bool:
        if amount > self.left:
            self.spent_out = True
            return False
        self.left -= amount
        return True


def column_index(letters: str) -> int:
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter) - ord("A") + 1
    return index - 1


def column_letters(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def as_number(text: str):
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def parse_iso(text: str):
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return text
    return moment.date() if moment.time() == datetime.min.time() and "T" not in text else moment


def is_date_format(format_id: int, format_code: str) -> bool:
    if format_id in BUILTIN_DATE_FORMATS:
        return True
    if not format_code or format_code.lower() == "general":
        return False
    return bool(DATE_LETTERS.search(QUOTED_OR_BRACKETED.sub("", format_code)))


def excel_date(number: Decimal, epoch: datetime):
    if number < 0 or number > LAST_EXCEL_SERIAL:
        return number
    moment = epoch + timedelta(days=float(number))
    moment = moment.replace(microsecond=0) if moment.microsecond < 500_000 else (moment + timedelta(seconds=1)).replace(microsecond=0)
    if number == number.to_integral_value():
        return moment.date()
    return moment.time() if number < 1 else moment


def sheet_row(cells: dict[int, object]) -> list:
    row = [None] * (max(cells) + 1)
    for index, value in cells.items():
        row[index] = value
    return row


def read_shared_strings(archive) -> list[str]:
    strings, state = [], {"buffer": [], "in_text": False, "phonetic": 0}

    def start(tag, _attributes):
        if tag == "si":
            state["buffer"] = []
        elif tag == "rPh":
            state["phonetic"] += 1
        elif tag == "t":
            state["in_text"] = True

    def end(tag):
        if tag == "t":
            state["in_text"] = False
        elif tag == "rPh":
            state["phonetic"] -= 1
        elif tag == "si":
            strings.append("".join(state["buffer"]))

    def text(data):
        if state["in_text"] and not state["phonetic"]:
            state["buffer"].append(data)

    xml_stream.walk(archive, "xl/sharedStrings.xml", start, end, text)
    return strings


def read_date_styles(archive) -> list[bool]:
    custom, styles = {}, []
    state = {"in_cell_xfs": False}

    def start(tag, attributes):
        if tag == "numFmt":
            custom[attributes.get("numFmtId", "")] = attributes.get("formatCode", "")
        elif tag == "cellXfs":
            state["in_cell_xfs"] = True
        elif tag == "xf" and state["in_cell_xfs"]:
            identifier = attributes.get("numFmtId", "0")
            styles.append(is_date_format(int(identifier) if identifier.isdigit() else 0, custom.get(identifier, "")))

    def end(tag):
        if tag == "cellXfs":
            state["in_cell_xfs"] = False

    xml_stream.walk(archive, "xl/styles.xml", start, end, lambda _data: None)
    return styles


def read_sheet_list(archive) -> tuple[list[dict], datetime]:
    found, epoch = [], EXCEL_EPOCH

    def start(tag, attributes):
        nonlocal epoch
        if tag == "sheet":
            found.append({"name": attributes.get("name", ""), "id": attributes.get("id", ""), "hidden": attributes.get("state", "visible") != "visible"})
        elif tag == "workbookPr" and attributes.get("date1904") in ("1", "true"):
            epoch = EXCEL_EPOCH_1904

    xml_stream.walk(archive, "xl/workbook.xml", start, lambda _tag: None, lambda _data: None)
    return found, epoch


def read_relationships(archive) -> dict[str, str]:
    targets = {}

    def start(tag, attributes):
        if tag == "Relationship":
            target = attributes.get("Target", "")
            targets[attributes.get("Id", "")] = posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join("xl", target))

    xml_stream.walk(archive, "xl/_rels/workbook.xml.rels", start, lambda _tag: None, lambda _data: None)
    return targets


def xlsx_cell_value(kind: str, raw: str, inline: str, shared: list[str], date_style: bool, epoch: datetime):
    if kind == "s":
        return shared[int(raw)] if raw.isdigit() and int(raw) < len(shared) else None
    if kind == "inlineStr":
        return inline
    if kind == "str":
        return raw
    if kind == "b":
        return raw == "1"
    if kind == "e":
        return ErrorValue(raw)
    if kind == "d":
        return parse_iso(raw)
    if not raw.strip():
        return None
    number = as_number(raw.strip())
    if number is None:
        return raw
    return excel_date(number, epoch) if date_style else number


def read_xlsx_sheet(archive, part: str, sheet: Sheet, shared, date_styles, epoch, budget: CellBudget):
    state = {"row": 0, "cells": {}, "col": 0, "kind": "", "style": 0, "ref_col": 0, "raw": [], "formula": [], "inline": [],
             "in_v": False, "in_f": False, "in_t": False, "in_is": False, "has_formula": False, "too_wide": False}

    def start(tag, attributes):
        if tag == "row":
            state["row"] = int(attributes["r"]) if attributes.get("r", "").isdigit() else state["row"] + 1
            state["cells"], state["col"] = {}, 0
        elif tag == "c":
            found = CELL_REFERENCE.match(attributes.get("r", ""))
            state["col"] = column_index(found.group(1)) if found else state["col"]
            state.update(kind=attributes.get("t", ""), style=int(attributes["s"]) if attributes.get("s", "").isdigit() else 0,
                         raw=[], formula=[], inline=[], has_formula=False)
        elif tag == "v":
            state["in_v"] = True
        elif tag == "f":
            state["in_f"], state["has_formula"] = True, True
        elif tag == "is":
            state["in_is"] = True
        elif tag == "t" and state["in_is"]:
            state["in_t"] = True

    def text(data):
        if state["in_v"]:
            state["raw"].append(data)
        elif state["in_f"]:
            state["formula"].append(data)
        elif state["in_t"]:
            state["inline"].append(data)

    def finish_cell():
        if state["col"] >= MAX_COLUMNS:
            sheet.cut_off = f"columns past {column_letters(MAX_COLUMNS - 1)} were not read"
            return
        style = state["style"]
        value = xlsx_cell_value(state["kind"], "".join(state["raw"]), "".join(state["inline"]), shared,
                                style < len(date_styles) and date_styles[style], epoch)
        if value is None:
            return
        if not budget.spend(1):
            sheet.cut_off = f"only the first {len(sheet.rows):,} rows were read (the file has more than {MAX_CELLS:,} cells in total)"
            raise StopReading()
        state["cells"][state["col"]] = value
        formula = "".join(state["formula"]).strip()
        if state["has_formula"] and formula and len(sheet.formulas) < MAX_FORMULAS:
            sheet.formulas[(state["row"], state["col"])] = "=" + formula

    def end(tag):
        if tag == "v":
            state["in_v"] = False
        elif tag == "f":
            state["in_f"] = False
        elif tag == "is":
            state["in_is"] = False
        elif tag == "t":
            state["in_t"] = False
        elif tag == "c":
            finish_cell()
            state["col"] += 1
        elif tag == "row" and state["cells"]:
            sheet.rows.append(sheet_row(state["cells"]))
            sheet.row_numbers.append(state["row"])

    xml_stream.walk(archive, part, start, end, text)


def load_xlsx(path: str) -> Workbook:
    archive = xml_stream.open_archive(path, "Excel (.xlsx)")
    with archive:
        names = xml_stream.part_names(archive)
        if "xl/workbook.xml" not in names:
            raise FileReadError("that isn't an Excel workbook (xl/workbook.xml is missing)")
        listed, epoch = read_sheet_list(archive)
        targets = read_relationships(archive) if "xl/_rels/workbook.xml.rels" in names else {}
        shared = read_shared_strings(archive) if "xl/sharedStrings.xml" in names else []
        date_styles = read_date_styles(archive) if "xl/styles.xml" in names else []
        budget, sheets = CellBudget(), []
        for number, entry in enumerate(listed, start=1):
            sheet = Sheet(entry["name"] or f"Sheet{number}", hidden=entry["hidden"])
            sheets.append(sheet)
            part = targets.get(entry["id"], f"xl/worksheets/sheet{number}.xml")
            if budget.spent_out:
                sheet.cut_off = "not read: the file has more than the cell limit"
            elif part in names:
                read_xlsx_sheet(archive, part, sheet, shared, date_styles, epoch, budget)
            else:
                sheet.cut_off = "this sheet's data is missing from the file"
    if not sheets:
        raise FileReadError("that workbook has no sheets")
    return Workbook(sheets, "xlsx")


def ods_value(attributes: dict, text: str):
    kind = attributes.get("value-type", "")
    if kind in ODS_NUMBER_TYPES:
        number = as_number(attributes.get("value", ""))
        return number if number is not None else text
    if kind == "date":
        return parse_iso(attributes.get("date-value", text))
    if kind == "time":
        return attributes.get("time-value", text)
    if kind == "boolean":
        return attributes.get("boolean-value") == "true"
    if kind == "string":
        return attributes.get("string-value", text)
    return text or None


def load_ods(path: str) -> Workbook:
    archive = xml_stream.open_archive(path, "OpenDocument spreadsheet (.ods)")
    budget, sheets = CellBudget(), []
    state = {"depth": 0, "sheet": None, "row": 0, "cells": {}, "col": 0, "row_repeat": 1, "cell": None, "paragraphs": [],
             "in_paragraph": 0, "annotation": 0}

    def start(tag, attributes):
        if tag == "table":
            state["depth"] += 1
            if state["depth"] == 1:
                state["sheet"] = Sheet(attributes.get("name", f"Sheet{len(sheets) + 1}"))
                sheets.append(state["sheet"])
                state["row"] = 0
        elif state["depth"] != 1:
            return
        elif tag == "table-row":
            state["cells"], state["col"] = {}, 0
            repeated = attributes.get("number-rows-repeated", "1")
            state["row_repeat"] = min(int(repeated) if repeated.isdigit() else 1, MAX_ROW_REPEAT)
        elif tag in ("table-cell", "covered-table-cell"):
            repeated = attributes.get("number-columns-repeated", "1")
            state["cell"] = {"attributes": attributes, "repeat": int(repeated) if repeated.isdigit() else 1}
            state["paragraphs"] = []
        elif tag == "annotation":
            state["annotation"] += 1
        elif tag == "p" and state["cell"] and not state["annotation"]:
            state["in_paragraph"] += 1
            state["paragraphs"].append("")
        elif tag == "s" and state["in_paragraph"]:
            count = attributes.get("c", "1")
            state["paragraphs"][-1] += " " * (int(count) if count.isdigit() else 1)
        elif tag == "tab" and state["in_paragraph"]:
            state["paragraphs"][-1] += "\t"

    def text(data):
        if state["in_paragraph"] and not state["annotation"]:
            state["paragraphs"][-1] += data

    def finish_cell():
        cell, sheet = state["cell"], state["sheet"]
        state["cell"] = None
        repeat = cell["repeat"]
        value = ods_value(cell["attributes"], "\n".join(state["paragraphs"]))
        if value is None or value == "":
            state["col"] += repeat
            return
        copies = min(repeat, MAX_CELL_REPEAT)
        if state["col"] + copies > MAX_COLUMNS:
            sheet.cut_off = f"columns past {column_letters(MAX_COLUMNS - 1)} were not read"
            state["col"] += repeat
            return
        for offset in range(copies):
            state["cells"][state["col"] + offset] = value
        formula = cell["attributes"].get("formula")
        if formula and len(sheet.formulas) < MAX_FORMULAS:
            sheet.formulas[(state["row"] + 1, state["col"])] = ODS_FORMULA_PREFIX.sub("", formula)
        state["col"] += repeat

    def finish_row():
        sheet, repeat = state["sheet"], state["row_repeat"]
        if state["cells"]:
            if not budget.spend(len(state["cells"]) * repeat):
                sheet.cut_off = f"only the first {len(sheet.rows):,} rows were read (the file has more than {MAX_CELLS:,} cells in total)"
                raise StopReading()
            for offset in range(repeat):
                sheet.rows.append(sheet_row(state["cells"]))
                sheet.row_numbers.append(state["row"] + 1 + offset)
        state["row"] += repeat

    def end(tag):
        if tag == "table":
            state["depth"] -= 1
        elif state["depth"] != 1:
            return
        elif tag in ("table-cell", "covered-table-cell") and state["cell"]:
            finish_cell()
        elif tag == "table-row":
            finish_row()
        elif tag == "annotation":
            state["annotation"] -= 1
        elif tag == "p" and state["in_paragraph"]:
            state["in_paragraph"] -= 1

    with archive:
        xml_stream.walk(archive, "content.xml", start, end, text)
    if not sheets:
        raise FileReadError("that spreadsheet has no sheets")
    return Workbook(sheets, "ods")


def text_encoding(sample: bytes) -> str:
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError as error:
        cut_mid_character = len(sample) == CSV_ENCODING_SAMPLE_BYTES and error.start >= len(sample) - UTF8_CHARACTER_BYTES
        if not cut_mid_character:
            return "cp1252"
    return "utf-8-sig"


def sniffed_delimiter(sample: str, extension: str) -> str:
    if extension == "tsv":
        return "\t"
    try:
        return csv.Sniffer().sniff(sample, delimiters=CSV_DELIMITERS).delimiter
    except csv.Error:
        return ","


def load_csv(path: str, extension: str) -> Workbook:
    csv.field_size_limit(CSV_FIELD_LIMIT)
    budget, sheet = CellBudget(), Sheet("Sheet1")
    try:
        with open(path, "rb") as handle:
            head = handle.read(CSV_ENCODING_SAMPLE_BYTES)
            encoding = text_encoding(head)
            sample = head[:CSV_SNIFF_CHARS * UTF8_CHARACTER_BYTES].decode(encoding, errors="replace")[:CSV_SNIFF_CHARS]
            handle.seek(0)
            wrapper = io.TextIOWrapper(handle, encoding=encoding, errors="replace", newline="")
            reader = csv.reader(wrapper, delimiter=sniffed_delimiter(sample, extension))
            for record in reader:
                cells = {index: value for index, value in enumerate(record[:MAX_COLUMNS]) if value.strip()}
                if not cells:
                    continue
                if not budget.spend(len(cells)):
                    sheet.cut_off = f"only the first {len(sheet.rows):,} rows were read (more than {MAX_CELLS:,} cells)"
                    break
                sheet.rows.append(sheet_row(cells))
                sheet.row_numbers.append(reader.line_num)
            wrapper.detach()
    except csv.Error as error:
        raise FileReadError(f"that file isn't readable as CSV ({error})") from None
    except OSError as error:
        raise FileReadError(f"couldn't open the file ({error.strerror or type(error).__name__})") from None
    return Workbook([sheet], "csv")


def format_of(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def load(path: str, name: str) -> Workbook:
    extension = format_of(name)
    if extension in ("xlsx", "xlsm"):
        return load_xlsx(path)
    if extension == "ods":
        return load_ods(path)
    if extension in ("csv", "tsv"):
        return load_csv(path, extension)
    if extension in OLD_EXCEL_FORMATS:
        raise FileReadError(f"{name} is an old Excel format; ask for it to be saved as .xlsx or .csv")
    raise FileReadError(f"{name} isn't a spreadsheet this reader opens (xlsx, ods, csv, tsv)")


def is_spreadsheet(name: str) -> bool:
    return format_of(name) in SPREADSHEET_FORMATS | OLD_EXCEL_FORMATS


def is_date_value(value) -> bool:
    return isinstance(value, (date, datetime))

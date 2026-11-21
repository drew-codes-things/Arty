import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Context, Decimal

from workbook import Sheet, Workbook, column_index, column_letters, parse_iso

MATH = Context(prec=80)
SHOWN_DECIMALS = 10
MAX_CELL_CHARS = 80
MAX_SHOWN_COLUMNS = 30
MAX_HEADER_NAMES = 15
DEFAULT_ROWS = 20
MAX_ROWS = 100
MAX_GROUPS = 100
MAX_FORMULAS_SHOWN = 50
TOP_VALUES = 3
OPERATIONS = ("sum", "average", "min", "max", "median", "count", "distinct")
NUMERIC_OPERATIONS = {"sum", "average", "median"}
OPERATOR_WORDS = {
    "=": "=", "==": "=", "is": "=", "equals": "=", "equal": "=", "!=": "!=", "<>": "!=", "not": "!=", "isnt": "!=",
    ">": ">", "greater": ">", "above": ">", "over": ">", ">=": ">=", "<": "<", "less": "<", "below": "<", "under": "<",
    "<=": "<=", "contains": "contains", "has": "contains", "includes": "contains",
}
NUMBER_TEXT = re.compile(r"^[-+]?[£$€]?\s*[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
BRACKETED_NEGATIVE = re.compile(r"^\((.+)\)$")
CELL_NAME = re.compile(r"^([A-Za-z]{1,3})$")


class CalcError(Exception):
    pass


@dataclass
class Table:
    sheet: Sheet
    header_row: int
    headers: list[str]
    rows: list[list]
    row_numbers: list[int]

    def width(self) -> int:
        return max(len(self.headers), max((len(row) for row in self.rows), default=0))


def numeric(value) -> Decimal | None:
    if isinstance(value, Decimal):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    negative = BRACKETED_NEGATIVE.match(text)
    if negative:
        text = "-" + negative.group(1).strip()
    if not NUMBER_TEXT.match(text):
        return None
    try:
        return Decimal(re.sub(r"[£$€,\s]", "", text))
    except ArithmeticError:
        return None


def plain(number: Decimal) -> str:
    if number == number.to_integral_value(context=MATH):
        return format(number.quantize(Decimal(1), context=MATH), "f")
    rounded = number.quantize(Decimal(1).scaleb(-SHOWN_DECIMALS), context=MATH)
    text = format(rounded, "f").rstrip("0").rstrip(".")
    return text if text not in ("", "-", "-0") else "0"


def shown(value, limit: int = MAX_CELL_CHARS) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, Decimal):
        return plain(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (date, time)):
        return value.isoformat()
    text = " / ".join(str(value).split("\n"))
    return text if len(text) <= limit else text[:limit - 1] + "..."


def full_text(value) -> str:
    return shown(value, limit=10_000)


def day_of(value):
    return value.date() if isinstance(value, datetime) else value


def as_day(text: str):
    found = parse_iso(text.strip())
    return day_of(found) if isinstance(found, (date, datetime)) else None


def pick_sheet(workbook: Workbook, wanted: str = "") -> Sheet:
    wanted = str(wanted or "").strip()
    if not wanted:
        return workbook.sheets[0]
    if wanted.isdigit() and 1 <= int(wanted) <= len(workbook.sheets):
        return workbook.sheets[int(wanted) - 1]
    exact = [sheet for sheet in workbook.sheets if sheet.name.casefold() == wanted.casefold()]
    partial = [sheet for sheet in workbook.sheets if wanted.casefold() in sheet.name.casefold()]
    found = exact or partial
    if len(found) == 1:
        return found[0]
    names = ", ".join(f'"{sheet.name}"' for sheet in workbook.sheets)
    raise CalcError(f'there is no single sheet called "{wanted}"; the sheets are {names}')


def build_table(sheet: Sheet, header_row: int | None = None) -> Table:
    if not sheet.rows:
        raise CalcError(f'sheet "{sheet.name}" is empty' + (f" ({sheet.cut_off})" if sheet.cut_off else ""))
    wanted = header_row if header_row else sheet.row_numbers[0]
    if wanted not in sheet.row_numbers:
        raise CalcError(f"row {wanted} of sheet \"{sheet.name}\" is empty or past the end, so it can't be the header row")
    position = sheet.row_numbers.index(wanted)
    header = sheet.rows[position]
    headers = [shown(cell).strip() or column_letters(index) for index, cell in enumerate(header)]
    return Table(sheet, wanted, headers, sheet.rows[position + 1:], sheet.row_numbers[position + 1:])


def resolve_column(table: Table, wanted) -> int:
    wanted = str(wanted or "").strip()
    if not wanted:
        raise CalcError("say which column; the columns are " + column_list(table))
    folded = [header.casefold() for header in table.headers]
    if wanted.casefold() in folded:
        return folded.index(wanted.casefold())
    letters = CELL_NAME.match(wanted)
    if letters and column_index(wanted.upper()) < table.width():
        return column_index(wanted.upper())
    partial = [index for index, header in enumerate(folded) if wanted.casefold() in header]
    if len(partial) == 1:
        return partial[0]
    if partial:
        raise CalcError(f'"{wanted}" matches several columns: ' + ", ".join(f'"{table.headers[index]}"' for index in partial))
    raise CalcError(f'no column called "{wanted}"; the columns are ' + column_list(table))


def column_list(table: Table) -> str:
    named = [f'{column_letters(index)} "{name}"' for index, name in enumerate(table.headers[:MAX_HEADER_NAMES])]
    more = f" and {len(table.headers) - MAX_HEADER_NAMES} more" if len(table.headers) > MAX_HEADER_NAMES else ""
    return ", ".join(named) + more


def cell_at(row: list, index: int):
    return row[index] if index < len(row) else None


def sheet_line(number: int, sheet: Sheet) -> str:
    first = f"rows {sheet.row_numbers[0]} to {sheet.row_numbers[-1]}" if sheet.rows else "empty"
    notes = (" (hidden)" if sheet.hidden else "") + (f" [{sheet.cut_off}]" if sheet.cut_off else "")
    headers = ""
    if sheet.rows:
        names = [shown(cell, 30) or column_letters(index) for index, cell in enumerate(sheet.rows[0][:MAX_HEADER_NAMES])]
        headers = "; first row: " + " | ".join(names)
    return f'{number}. "{sheet.name}": {len(sheet.rows)} non-empty rows ({first}), {sheet.width()} columns{notes}{headers}'


def overview(workbook: Workbook) -> str:
    lines = [f"{workbook.kind} workbook with {len(workbook.sheets)} sheet(s):"]
    lines += [sheet_line(number, sheet) for number, sheet in enumerate(workbook.sheets, start=1)]
    return "\n".join(lines)


def table_entries(table: Table) -> list[tuple[int, list]]:
    position = table.sheet.row_numbers.index(table.header_row)
    return list(zip(table.sheet.row_numbers, table.sheet.rows))[position:]


def rows_text(table: Table, start_row: int | None, count: int | None) -> str:
    count = max(1, min(int(count or DEFAULT_ROWS), MAX_ROWS))
    first = start_row if start_row else table.header_row
    chosen = [(number, row) for number, row in table_entries(table) if number >= first][:count]
    width = min(table.width(), MAX_SHOWN_COLUMNS)
    lines = [f'sheet "{table.sheet.name}", columns: ' + " | ".join(f"{column_letters(index)}={table.headers[index] if index < len(table.headers) else ''}" for index in range(width))]
    lines += [f"row {number}: " + " | ".join(shown(cell_at(row, index)) for index in range(width)) for number, row in chosen]
    last = chosen[-1][0] if chosen else first
    after = [number for number in table.row_numbers if number > last]
    if after:
        lines.append(f"(more rows: call again with start_row={after[0]})")
    if table.width() > width:
        lines.append(f"(only the first {width} of {table.width()} columns are shown)")
    return "\n".join(lines)


def column_kind(values: list) -> str:
    present = [value for value in values if value not in (None, "")]
    if not present:
        return "empty"
    if all(numeric(value) is not None for value in present):
        return "numbers"
    if all(isinstance(value, (date, datetime)) for value in present):
        return "dates"
    return "text"


def column_summary(table: Table, index: int) -> str:
    values = [cell_at(row, index) for row in table.rows]
    present = [value for value in values if value not in (None, "")]
    kind = column_kind(values)
    head = f'{column_letters(index)} "{table.headers[index] if index < len(table.headers) else ""}": {kind}, {len(present)} filled, {len(values) - len(present)} blank'
    if kind == "numbers":
        numbers = [numeric(value) for value in present]
        total = sum_of(numbers)
        return f"{head}; sum {plain(total)}, min {plain(min(numbers))}, max {plain(max(numbers))}, average {plain(MATH.divide(total, Decimal(len(numbers))))}"
    if kind == "dates":
        days = sorted(day_of(value) for value in present)
        return f"{head}; from {shown(days[0])} to {shown(days[-1])}"
    if kind == "text":
        counts: dict[str, int] = {}
        for value in present:
            counts[full_text(value)] = counts.get(full_text(value), 0) + 1
        top = sorted(counts.items(), key=lambda item: -item[1])[:TOP_VALUES]
        return f"{head}; {len(counts)} different values, most common: " + ", ".join(f'"{shown(name, 30)}" x{number}' for name, number in top)
    return head


def summary(table: Table) -> str:
    width = min(table.width(), MAX_SHOWN_COLUMNS)
    lines = [f'sheet "{table.sheet.name}": {len(table.rows)} data rows under the header in row {table.header_row}']
    lines += [column_summary(table, index) for index in range(width)]
    if table.width() > width:
        lines.append(f"(only the first {width} of {table.width()} columns are summarised)")
    return "\n".join(lines)


def sum_of(numbers: list[Decimal]) -> Decimal:
    total = Decimal(0)
    for number in numbers:
        total = MATH.add(total, number)
    return total


def median_of(numbers: list[Decimal]) -> Decimal:
    ordered = sorted(numbers)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return MATH.divide(MATH.add(ordered[middle - 1], ordered[middle]), Decimal(2))


def operator_for(word: str) -> str:
    key = str(word or "=").strip().casefold().replace(" ", "")
    key = {"greaterthan": "greater", "lessthan": "less", "notequal": "!=", "notequals": "!=", "isnot": "!=", "equalto": "="}.get(key, key)
    if key not in OPERATOR_WORDS:
        raise CalcError('the filter operator must be one of =, !=, >, >=, <, <=, contains')
    return OPERATOR_WORDS[key]


def passes(value, operator: str, wanted: str) -> bool:
    if operator == "contains":
        return wanted.casefold() in full_text(value).casefold()
    number, wanted_number = numeric(value), numeric(wanted)
    if number is not None and wanted_number is not None:
        left, right = number, wanted_number
    elif isinstance(value, (date, datetime)) and as_day(wanted):
        left, right = day_of(value), as_day(wanted)
    else:
        left, right = full_text(value).casefold(), wanted.casefold()
    return {"=": left == right, "!=": left != right, ">": left > right, ">=": left >= right,
            "<": left < right, "<=": left <= right}[operator]


def filtered(table: Table, filter_column, filter_operator, filter_value) -> tuple[list[list], str]:
    if not str(filter_column or "").strip():
        return table.rows, ""
    index = resolve_column(table, filter_column)
    operator = operator_for(filter_operator)
    wanted = str(filter_value if filter_value is not None else "")
    rows = [row for row in table.rows if passes(cell_at(row, index), operator, wanted)]
    return rows, f' where "{table.headers[index]}" {operator} {wanted}'


def measure(operation: str, values: list, column_name: str) -> tuple[str, str]:
    present = [value for value in values if value not in (None, "")]
    if operation == "count":
        return str(len(present)), f"{len(present)} filled cells"
    if operation == "distinct":
        return str(len({full_text(value).casefold() for value in present})), ""
    numbers = [numeric(value) for value in present]
    usable = [number for number in numbers if number is not None]
    skipped = len(present) - len(usable)
    note = f"{len(usable)} numbers used" + (f", {skipped} non-number cells skipped" if skipped else "")
    if operation in NUMERIC_OPERATIONS or (operation in ("min", "max") and usable and not skipped):
        if not usable:
            raise CalcError(f'"{column_name}" has no numbers to {operation}')
        result = {"sum": lambda: sum_of(usable), "average": lambda: MATH.divide(sum_of(usable), Decimal(len(usable))),
                  "median": lambda: median_of(usable), "min": lambda: min(usable), "max": lambda: max(usable)}[operation]()
        return plain(result), note
    dates = [day_of(value) for value in present if isinstance(value, (date, datetime))]
    if dates and len(dates) == len(present):
        return shown(min(dates) if operation == "min" else max(dates)), f"{len(dates)} dates used"
    raise CalcError(f'"{column_name}" holds text, so {operation} needs a column of numbers or dates')


def calculate(table: Table, operation: str, column, group_by="", filter_column="", filter_operator="=", filter_value="") -> str:
    operation = str(operation or "").strip().casefold()
    operation = {"total": "sum", "mean": "average", "avg": "average", "minimum": "min", "maximum": "max", "unique": "distinct"}.get(operation, operation)
    if operation not in OPERATIONS:
        raise CalcError("operation must be one of " + ", ".join(OPERATIONS))
    index = resolve_column(table, column)
    name = table.headers[index]
    rows, where = filtered(table, filter_column, filter_operator, filter_value)
    scope = f'sheet "{table.sheet.name}"{where}: {len(rows)} data rows'
    if not str(group_by or "").strip():
        result, note = measure(operation, [cell_at(row, index) for row in rows], name)
        return f'{operation} of "{name}" = {result}\n({scope}; {note or "done"}; worked out by the program, exactly)'
    group_index = resolve_column(table, group_by)
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(full_text(cell_at(row, group_index)) or "(blank)", []).append(cell_at(row, index))
    lines = [f'{operation} of "{name}" for each "{table.headers[group_index]}" ({scope}):']
    for label, values in list(groups.items())[:MAX_GROUPS]:
        try:
            result, _ = measure(operation, values, name)
        except CalcError as error:
            result = f"({error})"
        lines.append(f"{shown(label, 60)}: {result}  ({len(values)} rows)")
    if len(groups) > MAX_GROUPS:
        lines.append(f"(only the first {MAX_GROUPS} of {len(groups)} groups are shown)")
    lines.append("(worked out by the program, exactly)")
    return "\n".join(lines)


def formulas_text(sheet: Sheet, start: int | None, count: int | None) -> str:
    if not sheet.formulas:
        return f'sheet "{sheet.name}" has no formulas (every cell is a plain value)'
    by_row = dict(zip(sheet.row_numbers, sheet.rows))
    ordered = sorted(sheet.formulas.items())
    first = max(1, int(start or 1))
    shown_count = max(1, min(int(count or MAX_FORMULAS_SHOWN), MAX_FORMULAS_SHOWN))
    chosen = ordered[first - 1:first - 1 + shown_count]
    lines = [f'sheet "{sheet.name}" has {len(ordered)} formulas; showing {first} to {first + len(chosen) - 1}:']
    for (row_number, column), formula in chosen:
        value = cell_at(by_row.get(row_number, []), column)
        lines.append(f"{column_letters(column)}{row_number}: {shown(formula, 150)} -> {shown(value)}")
    if first + len(chosen) <= len(ordered):
        lines.append(f"(more: call again with start={first + len(chosen)})")
    return "\n".join(lines)

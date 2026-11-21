import csv
import io
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

XLSX_NAMESPACE = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RELATIONSHIPS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
ODF_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
ODF_TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
ODF_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
SALES_HEADER = ["Region", "Product", "Units", "Price", "Day"]
SALES_ROWS = [
    ["North", "Widget", 10, "2.5", 45292],
    ["South", "Widget", 4, "2.5", 45293],
    ["North", "Gadget", 3, "10.1", 45294],
    ["East", "Gadget", 7, "10.1", 45295],
    ["North", "Widget", 6, "2.5", 45296],
]


def column_name(index):
    return "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[index]


def xlsx_cell(reference, value, shared, style=0, formula=None):
    formula_xml = f"<f>{escape(formula)}</f>" if formula else ""
    style_attribute = f' s="{style}"' if style else ""
    if isinstance(value, str):
        if formula:
            return f'<c r="{reference}" t="str"{style_attribute}>{formula_xml}<v>{escape(value)}</v></c>'
        shared.append(value)
        return f'<c r="{reference}" t="s"{style_attribute}><v>{len(shared) - 1}</v></c>'
    return f'<c r="{reference}"{style_attribute}>{formula_xml}<v>{value}</v></c>'


def sheet_xml(rows, shared, formulas=None, date_column=None):
    formulas = formulas or {}
    body = []
    for row_number, row in enumerate(rows, start=1):
        cells = []
        for index, value in enumerate(row):
            if value is None:
                continue
            reference = f"{column_name(index)}{row_number}"
            style = 1 if date_column is not None and index == date_column and row_number > 1 else 0
            cells.append(xlsx_cell(reference, value, shared, style, formulas.get(reference)))
        body.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    return (f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{XLSX_NAMESPACE}"><sheetData>'
            f'{"".join(body)}</sheetData></worksheet>')


def write_xlsx(path, sheets, formulas=None, date_column=None, hidden=()):
    shared = []
    names = list(sheets)
    sheet_parts = {f"xl/worksheets/sheet{number}.xml": sheet_xml(sheets[name], shared, (formulas or {}).get(name), date_column)
                   for number, name in enumerate(names, start=1)}
    listed = "".join(
        f'<sheet name="{escape(name)}" sheetId="{number}"{" state=" + chr(34) + "hidden" + chr(34) if name in hidden else ""} r:id="rId{number}"/>'
        for number, name in enumerate(names, start=1))
    relationships = "".join(
        f'<Relationship Id="rId{number}" Type="worksheet" Target="worksheets/sheet{number}.xml"/>' for number in range(1, len(names) + 1))
    shared_xml = "".join(f"<si><t>{escape(text)}</t></si>" for text in shared)
    styles = (f'<styleSheet xmlns="{XLSX_NAMESPACE}"><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="{XLSX_NAMESPACE}" xmlns:r="{RELATIONSHIPS}"><sheets>{listed}</sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", f"<Relationships>{relationships}</Relationships>")
        archive.writestr("xl/sharedStrings.xml", f'<sst xmlns="{XLSX_NAMESPACE}">{shared_xml}</sst>')
        archive.writestr("xl/styles.xml", styles)
        for part, xml in sheet_parts.items():
            archive.writestr(part, xml)
    return Path(path)


def ods_cell(value, formula=None):
    formula_attribute = f' table:formula="{escape(formula)}"' if formula else ""
    if value is None:
        return "<table:table-cell/>"
    if isinstance(value, str):
        return f'<table:table-cell office:value-type="string"{formula_attribute}><text:p>{escape(value)}</text:p></table:table-cell>'
    return f'<table:table-cell office:value-type="float" office:value="{value}"{formula_attribute}><text:p>{value}</text:p></table:table-cell>'


def write_ods(path, sheets, formulas=None, trailing_blank_rows=0):
    tables = []
    for name, rows in sheets.items():
        body = []
        for row_number, row in enumerate(rows, start=1):
            cells = "".join(ods_cell(value, (formulas or {}).get(name, {}).get(f"{column_name(index)}{row_number}")) for index, value in enumerate(row))
            body.append(f"<table:table-row>{cells}</table:table-row>")
        if trailing_blank_rows:
            body.append(f'<table:table-row table:number-rows-repeated="{trailing_blank_rows}"><table:table-cell table:number-columns-repeated="16384"/></table:table-row>')
        tables.append(f'<table:table table:name="{escape(name)}">{"".join(body)}</table:table>')
    content = (f'<?xml version="1.0" encoding="UTF-8"?><office:document-content xmlns:office="{ODF_OFFICE}" xmlns:table="{ODF_TABLE}" '
               f'xmlns:text="{ODF_TEXT}"><office:body><office:spreadsheet>{"".join(tables)}</office:spreadsheet></office:body></office:document-content>')
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        archive.writestr("content.xml", content)
    return Path(path)


def write_csv(path, rows, delimiter=","):
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=delimiter).writerows(rows)
    Path(path).write_text(buffer.getvalue(), encoding="utf-8")
    return Path(path)


def write_docx(path, paragraphs, table=None, extra_parts=None):
    body = "".join(f'<w:p><w:pPr><w:tabs><w:tab w:val="left" w:pos="720"/></w:tabs></w:pPr><w:r><w:t>{escape(text)}</w:t></w:r></w:p>' for text in paragraphs)
    if table:
        rows = "".join("<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{escape(cell)}</w:t></w:r></w:p></w:tc>" for cell in row) + "</w:tr>" for row in table)
        body += f"<w:tbl>{rows}</w:tbl>"
    document = f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{WORD_NAMESPACE}"><w:body>{body}</w:body></w:document>'
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document)
        for name, data in (extra_parts or {}).items():
            archive.writestr(name, data)
    return Path(path)


def write_odt(path, paragraphs, comment=None):
    body = "".join(f"<text:p>{escape(text)}</text:p>" for text in paragraphs)
    if comment:
        body += f"<text:p>Visible<office:annotation><text:p>{escape(comment)}</text:p></office:annotation></text:p>"
    content = (f'<?xml version="1.0" encoding="UTF-8"?><office:document-content xmlns:office="{ODF_OFFICE}" xmlns:text="{ODF_TEXT}">'
               f"<office:body><office:text>{body}</office:text></office:body></office:document-content>")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        archive.writestr("content.xml", content)
    return Path(path)


def write_pdf(path, pages):
    objects = []

    def add(body):
        objects.append(body)
        return len(objects)

    catalog = add(None)
    page_tree = add(None)
    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids = []
    for text in pages:
        stream = b"" if text is None else f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_ids.append(add(f"<< /Type /Page /Parent {page_tree} 0 R /MediaBox [0 0 612 792] /Contents {content} 0 R "
                            f"/Resources << /Font << /F1 {font} 0 R >> >> >>".encode()))
    objects[catalog - 1] = f"<< /Type /Catalog /Pages {page_tree} 0 R >>".encode()
    kids = " ".join(f"{number} 0 R" for number in page_ids)
    objects[page_tree - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()
    output = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    table_start = len(output)
    output += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        output += f"{offset:010d} 00000 n \n".encode()
    output += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{table_start}\n%%EOF\n".encode()
    Path(path).write_bytes(bytes(output))
    return Path(path)

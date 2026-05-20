import os
import re
import zipfile
import xml.etree.ElementTree as ET


NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "office_rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


def _read_xml(archive, member):
    try:
        with archive.open(member) as f:
            return ET.parse(f).getroot()
    except KeyError:
        return None


def _text_content(element):
    if element is None:
        return ""
    return "".join(element.itertext())


def _shared_strings(archive):
    root = _read_xml(archive, "xl/sharedStrings.xml")
    if root is None:
        return []
    return [_text_content(si) for si in root.findall("main:si", NS)]


def _sheet_paths(archive):
    workbook = _read_xml(archive, "xl/workbook.xml")
    rels = _read_xml(archive, "xl/_rels/workbook.xml.rels")
    if workbook is None or rels is None:
        raise ValueError("Invalid xlsx file, missing workbook metadata")

    rid_to_target = {}
    for rel in rels.findall("rel:Relationship", NS):
        rid = rel.attrib.get("Id")
        target = rel.attrib.get("Target", "")
        if rid:
            rid_to_target[rid] = target if target.startswith("xl/") else f"xl/{target.lstrip('/')}"

    sheets = []
    for sheet in workbook.findall("main:sheets/main:sheet", NS):
        name = sheet.attrib.get("name", "Sheet")
        rid = sheet.attrib.get(f"{{{NS['office_rel']}}}id")
        path = rid_to_target.get(rid)
        if path:
            sheets.append((name, path))
    return sheets


def _column_number(cell_ref):
    match = re.match(r"([A-Z]+)", cell_ref or "")
    if not match:
        return 0
    number = 0
    for char in match.group(1):
        number = number * 26 + ord(char) - ord("A") + 1
    return number


def _cell_text(cell, shared):
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return _text_content(cell.find("main:is", NS))
    value = cell.find("main:v", NS)
    raw = value.text if value is not None and value.text is not None else ""
    if cell_type == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return raw
    if cell_type == "b":
        return "TRUE" if raw == "1" else "FALSE"
    return raw


def _iter_sheet_cells(root, shared):
    rows = []
    for row in root.findall(".//main:sheetData/main:row", NS):
        row_index = int(row.attrib.get("r", len(rows) + 1))
        cells = []
        for cell in row.findall("main:c", NS):
            cell_ref = cell.attrib.get("r", "")
            text = _cell_text(cell, shared)
            if text == "":
                continue
            cells.append((cell_ref, _column_number(cell_ref), text))
        if cells:
            cells.sort(key=lambda item: item[1])
            rows.append((row_index, cells))
    rows.sort(key=lambda item: item[0])
    return rows


def _append_text(parts, text):
    start = sum(len(part) for part in parts)
    parts.append(text)
    return start, start + len(text)


def build_xlsx_document(xlsx_path, doc_id=None):
    char_parts = []
    segments = []
    structure = []

    with zipfile.ZipFile(xlsx_path) as archive:
        shared = _shared_strings(archive)
        for sheet_name, sheet_path in _sheet_paths(archive):
            root = _read_xml(archive, sheet_path)
            if root is None:
                continue
            if char_parts:
                char_parts.append("\n")

            sheet_start = sum(len(part) for part in char_parts)
            first_cell = None
            last_cell = None
            for row_offset, (row_index, cells) in enumerate(_iter_sheet_cells(root, shared)):
                if row_offset:
                    char_parts.append("\n")
                for cell_offset, (cell_ref, _col, text) in enumerate(cells):
                    if cell_offset:
                        char_parts.append("\t")
                    start_char, end_char = _append_text(char_parts, text)
                    first_cell = first_cell or cell_ref
                    last_cell = cell_ref
                    segments.append(
                        {
                            "segment_id": len(segments),
                            "start_char": start_char,
                            "end_char": end_char,
                            "text": text,
                            "locator": {
                                "type": "xlsx",
                                "sheet": sheet_name,
                                "cell": cell_ref,
                                "cell_offset": 0,
                            },
                        }
                    )
            sheet_end = sum(len(part) for part in char_parts)
            if sheet_end > sheet_start:
                node = {
                    "title": sheet_name,
                    "node_id": str(len(structure)).zfill(4),
                    "start_char": sheet_start,
                    "end_char": sheet_end,
                    "text": "".join(char_parts)[sheet_start:sheet_end],
                }
                if first_cell and last_cell:
                    node["start_cell"] = first_cell
                    node["end_cell"] = last_cell
                structure.append(node)

    char_stream = "".join(char_parts)
    if not structure and char_stream:
        structure = [
            {
                "title": "Workbook",
                "node_id": "0000",
                "start_char": 0,
                "end_char": len(char_stream),
                "text": char_stream,
            }
        ]

    return {
        "id": doc_id or "",
        "type": "xlsx",
        "path": os.path.abspath(xlsx_path),
        "doc_name": os.path.basename(xlsx_path),
        "doc_description": "",
        "total_chars": len(char_stream),
        "structure": structure,
        "segments": segments,
        "char_stream": char_stream,
    }


import os
import re
import zipfile
import xml.etree.ElementTree as ET


WORD_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}


def _qn(name):
    return f"{{{WORD_NS['w']}}}{name}"


def _read_xml(docx_path, member):
    try:
        with zipfile.ZipFile(docx_path) as archive:
            with archive.open(member) as f:
                return ET.parse(f).getroot()
    except KeyError:
        return None


def _style_name_map(docx_path):
    root = _read_xml(docx_path, "word/styles.xml")
    if root is None:
        return {}
    mapping = {}
    for style in root.findall("w:style", WORD_NS):
        style_id = style.attrib.get(_qn("styleId"))
        name_el = style.find("w:name", WORD_NS)
        if style_id and name_el is not None:
            mapping[style_id] = name_el.attrib.get(_qn("val"), "")
    return mapping


def _paragraph_text(paragraph):
    parts = []
    for node in paragraph.iter():
        if node.tag == _qn("t") and node.text:
            parts.append(node.text)
        elif node.tag == _qn("tab"):
            parts.append("\t")
        elif node.tag == _qn("br"):
            parts.append("\n")
    return "".join(parts)


def _paragraph_style(paragraph, style_names):
    p_style = paragraph.find("w:pPr/w:pStyle", WORD_NS)
    style_id = p_style.attrib.get(_qn("val")) if p_style is not None else None
    return style_id, style_names.get(style_id, style_id or "")


def extract_docx_paragraphs(docx_path):
    root = _read_xml(docx_path, "word/document.xml")
    if root is None:
        raise ValueError(f"Invalid docx file, missing word/document.xml: {docx_path}")

    style_names = _style_name_map(docx_path)
    paragraphs = []
    for index, paragraph in enumerate(root.findall(".//w:p", WORD_NS)):
        text = _paragraph_text(paragraph)
        if not text.strip():
            continue
        style_id, style = _paragraph_style(paragraph, style_names)
        paragraphs.append(
            {
                "paragraph": index,
                "text": text,
                "style": style,
                "style_id": style_id,
            }
        )
    return paragraphs


def _heading_level(style, style_id):
    candidates = [style or "", style_id or ""]
    for value in candidates:
        normalized = value.replace("_", " ").replace("-", " ").strip()
        match = re.search(r"\bheading\s*([1-9])\b", normalized, re.IGNORECASE)
        if match:
            return int(match.group(1))
        match = re.fullmatch(r"h([1-9])", normalized, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _build_char_stream(paragraphs):
    char_parts = []
    segments = []
    heading_items = []

    for paragraph in paragraphs:
        if char_parts:
            char_parts.append("\n")
        start_char = sum(len(part) for part in char_parts)
        text = paragraph["text"]
        end_char = start_char + len(text)
        locator = {
            "type": "docx",
            "paragraph": paragraph["paragraph"],
            "char_offset": 0,
        }
        segment = {
            "segment_id": len(segments),
            "start_char": start_char,
            "end_char": end_char,
            "text": text,
            "locator": locator,
        }
        segments.append(segment)

        level = _heading_level(paragraph.get("style"), paragraph.get("style_id"))
        if level is not None:
            heading_items.append(
                {
                    "level": level,
                    "title": text.strip(),
                    "start_char": start_char,
                    "paragraph": paragraph["paragraph"],
                }
            )
        char_parts.append(text)

    char_stream = "".join(char_parts)
    return char_stream, segments, heading_items


def _build_heading_tree(heading_items, total_chars):
    root = []
    stack = []

    for item in heading_items:
        node = {
            "title": item["title"],
            "start_char": item["start_char"],
            "end_char": total_chars,
            "nodes": [],
        }
        while stack and stack[-1]["level"] >= item["level"]:
            stack.pop()
        if stack:
            stack[-1]["node"]["nodes"].append(node)
        else:
            root.append(node)
        stack.append({"level": item["level"], "node": node})

    def fill_ends(nodes, parent_end):
        for index, node in enumerate(nodes):
            node["end_char"] = nodes[index + 1]["start_char"] if index + 1 < len(nodes) else parent_end
            if node.get("nodes"):
                fill_ends(node["nodes"], node["end_char"])

    fill_ends(root, total_chars)
    return root


def _add_node_text(nodes, char_stream):
    for node in nodes:
        node["text"] = char_stream[node["start_char"]:node["end_char"]]
        if node.get("nodes"):
            _add_node_text(node["nodes"], char_stream)


def _write_node_id(nodes, start=0):
    current = start
    for node in nodes:
        node["node_id"] = str(current).zfill(4)
        current += 1
        if node.get("nodes"):
            current = _write_node_id(node["nodes"], current)
        if not node.get("nodes"):
            node.pop("nodes", None)
    return current


def build_docx_document(docx_path, doc_id=None):
    paragraphs = extract_docx_paragraphs(docx_path)
    char_stream, segments, heading_items = _build_char_stream(paragraphs)
    structure = _build_heading_tree(heading_items, len(char_stream))

    if not structure and char_stream:
        structure = [
            {
                "title": "Document",
                "start_char": 0,
                "end_char": len(char_stream),
                "nodes": [],
            }
        ]

    _add_node_text(structure, char_stream)
    _write_node_id(structure)

    return {
        "id": doc_id or "",
        "type": "docx",
        "path": os.path.abspath(docx_path),
        "doc_name": os.path.basename(docx_path),
        "doc_description": "",
        "total_chars": len(char_stream),
        "structure": structure,
        "segments": segments,
        "char_stream": char_stream,
    }

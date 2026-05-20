import os
import re


def _line_start_offsets(text):
    offsets = []
    cursor = 0
    for line in text.splitlines(keepends=True):
        offsets.append(cursor)
        cursor += len(line)
    if not offsets:
        offsets.append(0)
    return offsets


def _heading_items(lines, offsets):
    items = []
    in_code_block = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            items.append(
                {
                    "level": len(match.group(1)),
                    "title": match.group(2).strip(),
                    "line_num": index + 1,
                    "start_char": offsets[index],
                }
            )
    return items


def _build_segments(lines, offsets):
    segments = []
    for index, line in enumerate(lines):
        text = line.rstrip("\n")
        end_char = offsets[index] + len(text)
        segments.append(
            {
                "segment_id": len(segments),
                "start_char": offsets[index],
                "end_char": end_char,
                "text": text,
                "locator": {
                    "type": "md",
                    "line_num": index + 1,
                    "char_offset": 0,
                },
            }
        )
    return segments


def _build_tree(items, total_chars):
    root = []
    stack = []
    for item in items:
        node = {
            "title": item["title"],
            "line_num": item["line_num"],
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


def _add_text_and_ids(nodes, char_stream, start=0):
    current = start
    for node in nodes:
        node["text"] = char_stream[node["start_char"]:node["end_char"]]
        node["node_id"] = str(current).zfill(4)
        current += 1
        if node.get("nodes"):
            current = _add_text_and_ids(node["nodes"], char_stream, current)
        if not node.get("nodes"):
            node.pop("nodes", None)
    return current


def build_md_document(md_path, doc_id=None):
    with open(md_path, "r", encoding="utf-8") as f:
        char_stream = f.read()
    lines = char_stream.splitlines(keepends=True)
    if not lines:
        lines = [""]

    offsets = _line_start_offsets(char_stream)
    segments = _build_segments(lines, offsets)
    items = _heading_items(lines, offsets)
    structure = _build_tree(items, len(char_stream))
    if not structure:
        structure = [
            {
                "title": "Document",
                "line_num": 1,
                "start_char": 0,
                "end_char": len(char_stream),
                "nodes": [],
            }
        ]
    _add_text_and_ids(structure, char_stream)

    return {
        "id": doc_id or "",
        "type": "md",
        "path": os.path.abspath(md_path),
        "doc_name": os.path.basename(md_path),
        "doc_description": "",
        "line_count": char_stream.count("\n") + 1 if char_stream else 0,
        "total_chars": len(char_stream),
        "structure": structure,
        "segments": segments,
        "char_stream": char_stream,
    }


import re


TITLE_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"(?:第\s*[一二三四五六七八九十百千万\d]+\s*[章节篇部分])|"
    r"(?:\d+(?:\.\d+){0,5}\s+)|"
    r"(?:[A-Z]\.\s+)"
    r")"
)
KNOWN_TITLE_RE = re.compile(
    r"^(?:abstract|references|conclusion|appendix|discussion|related work|"
    r"acknowledg(?:e)?ments?|introduction|method|methods|experiments?|analysis|"
    r"contributions?)$",
    re.I,
)


def _font_threshold(segments):
    sizes = sorted([s.get("font_size") for s in segments if s.get("font_size")], reverse=True)
    if not sizes:
        return None
    body = sorted(sizes)[len(sizes) // 2]
    return max(body * 1.15, body + 1.0)


def _title_level(text, font_size, threshold):
    stripped = text.strip()
    if not stripped:
        return None
    match = re.match(r"^(\d+(?:\.\d+)*)\s+(.+)$", stripped)
    if match:
        rest = match.group(2).strip()
        if len(rest) > 90:
            return None
        if "." not in match.group(1) and not KNOWN_TITLE_RE.match(rest):
            return None
        if not re.match(r"^[A-Za-z\u4e00-\u9fff]", rest):
            return None
        if re.search(r"[←→≤≥=∈{}\\]", rest):
            return None
        return min(match.group(1).count(".") + 1, 6)
    if re.match(r"^第\s*[一二三四五六七八九十百千万\d]+\s*篇", stripped):
        return 1
    if re.match(r"^第\s*[一二三四五六七八九十百千万\d]+\s*章", stripped):
        return 2
    if re.match(r"^第\s*[一二三四五六七八九十百千万\d]+\s*节", stripped):
        return 3
    match = re.match(r"^[A-Z]\.\s+(.+)$", stripped)
    if match:
        rest = match.group(1).strip()
        if KNOWN_TITLE_RE.match(rest) or len(rest.split()) >= 2:
            return 1
        return None
    if KNOWN_TITLE_RE.match(stripped):
        return 1
    return None


def _candidate_titles(char_index):
    segments = char_index.get("segments", [])
    threshold = _font_threshold(segments)
    items = []
    for segment in segments:
        text = segment.get("text", "").strip()
        if not text:
            continue
        level = _title_level(text, segment.get("font_size"), threshold)
        if level is None:
            continue
        if len(text) > 160:
            continue
        if not TITLE_PREFIX_RE.match(text) and not KNOWN_TITLE_RE.match(text):
            continue
        items.append(
            {
                "level": level,
                "title": text,
                "start_char": segment["start_char"],
            }
        )
    return _dedupe_adjacent(items)


def _dedupe_adjacent(items):
    result = []
    for item in items:
        if result and item["title"] == result[-1]["title"] and item["start_char"] - result[-1]["start_char"] < 10:
            continue
        result.append(item)
    return result


def _build_tree(items, total_chars):
    root = []
    stack = []
    for item in items:
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


def _add_text_ids_and_pages(nodes, char_stream, pages, start=0):
    current = start
    for node in nodes:
        node["node_id"] = str(current).zfill(4)
        current += 1
        node["text"] = char_stream[node["start_char"]:node["end_char"]]
        start_page = _page_for_char(pages, node["start_char"])
        end_page = _page_for_char(pages, max(node["end_char"] - 1, node["start_char"]))
        if start_page is not None:
            node["start_index"] = start_page
        if end_page is not None:
            node["end_index"] = end_page
        if node.get("nodes"):
            current = _add_text_ids_and_pages(node["nodes"], char_stream, pages, current)
        if not node.get("nodes"):
            node.pop("nodes", None)
    return current


def _page_for_char(pages, char_pos):
    for page in pages:
        if page.get("start_char", 0) <= char_pos <= page.get("end_char", 0):
            return page.get("page")
    return pages[-1].get("page") if pages else None


def build_pdf_heuristic_structure(char_index):
    char_stream = char_index.get("char_stream", "")
    pages = char_index.get("pages", [])
    items = _candidate_titles(char_index)
    structure = _build_tree(items, len(char_stream))
    if not structure and char_stream:
        structure = [
            {
                "title": "Document",
                "start_char": 0,
                "end_char": len(char_stream),
                "nodes": [],
            }
        ]
    _add_text_ids_and_pages(structure, char_stream, pages)
    return structure

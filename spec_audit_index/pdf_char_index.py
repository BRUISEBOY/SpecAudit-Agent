import re
from io import BytesIO
from copy import deepcopy


def _open_pdf_reader(pdf_path):
    try:
        import PyPDF2
    except ImportError as exc:
        raise ImportError("PyPDF2 is required to build the PDF char-level index") from exc
    with open(pdf_path, "rb") as f:
        return PyPDF2.PdfReader(BytesIO(f.read()))


def _open_pymupdf(pdf_path):
    try:
        import pymupdf
    except ImportError:
        try:
            import fitz as pymupdf
        except ImportError as exc:
            raise ImportError("PyMuPDF is required to build PDF bbox locators") from exc
    return pymupdf.open(pdf_path)


def _span_bbox(text, tm, font_size):
    x = float(tm[4]) if len(tm) > 4 else 0.0
    y = float(tm[5]) if len(tm) > 5 else 0.0
    size = float(font_size or 0.0)
    # PyPDF2 visitor callbacks do not expose glyph widths consistently. This
    # estimate is still useful for jumping/highlighting near the matched text.
    width = max(size * 0.45 * len(text), size)
    return [x, y - size, x + width, y]


def _extract_page_spans(page):
    spans = []

    def visitor_text(text, cm, tm, font_dict, font_size):
        if not text:
            return
        spans.append(
            {
                "text": text,
                "bbox": _span_bbox(text, tm, font_size),
                "font_size": float(font_size or 0.0),
            }
        )

    try:
        page.extract_text(visitor_text=visitor_text)
    except TypeError:
        return []
    return spans


def _merge_span_bboxes(spans):
    boxes = [span["bbox"] for span in spans if span.get("bbox")]
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _extract_page_line_spans_pymupdf(page):
    """Extract readable line segments with real page bboxes via PyMuPDF."""
    page_dict = page.get_text("dict")
    segments = []
    text_blocks = [block for block in page_dict.get("blocks", []) if block.get("type") == 0]
    text_blocks.sort(key=lambda block: (block.get("bbox", [0, 0, 0, 0])[1], block.get("bbox", [0, 0, 0, 0])[0]))

    for block in text_blocks:
        lines = block.get("lines", [])
        lines.sort(key=lambda line: (line.get("bbox", [0, 0, 0, 0])[1], line.get("bbox", [0, 0, 0, 0])[0]))
        for line in lines:
            spans = [span for span in line.get("spans", []) if span.get("text")]
            if not spans:
                continue
            spans.sort(key=lambda span: span.get("bbox", [0, 0, 0, 0])[0])
            text = "".join(span.get("text", "") for span in spans).rstrip()
            if not text:
                continue
            segments.append(
                {
                    "text": text + "\n",
                    "bbox": _merge_span_bboxes(spans),
                    "font_size": max(float(span.get("size") or 0.0) for span in spans),
                }
            )
    return segments


def _fallback_spans_from_text(page_text):
    if not page_text:
        return []
    return [{"text": part, "bbox": None, "font_size": None} for part in page_text.splitlines(keepends=True)]


def build_pdf_char_index(pdf_path, extract_bboxes=False):
    """
    Build the format-neutral char stream for a PDF.

    Output intentionally matches the unified schema in char_level_index_design.md:
    full text is represented by char offsets, while each segment carries a typed
    PDF locator. If PyPDF2 cannot provide visitor spans, we fall back to one
    locator per physical page.
    """
    doc = None
    reader = None
    if extract_bboxes:
        try:
            doc = _open_pymupdf(pdf_path)
        except ImportError:
            doc = None
    if doc is None:
        reader = _open_pdf_reader(pdf_path)
    char_parts = []
    pages = []
    segments = []

    total_pages = len(doc) if doc is not None else len(reader.pages)
    for page_number in range(1, total_pages + 1):
        if page_number > 1:
            char_parts.append("\n")

        page_start = sum(len(part) for part in char_parts)
        if doc is not None:
            spans = _extract_page_line_spans_pymupdf(doc[page_number - 1])
        else:
            page = reader.pages[page_number - 1]
            spans = _extract_page_spans(page) if extract_bboxes else []
        page_text = "".join(span["text"] for span in spans)
        if not page_text:
            page = reader.pages[page_number - 1] if reader is not None else _open_pdf_reader(pdf_path).pages[page_number - 1]
            page_text = page.extract_text() or ""
            spans = _fallback_spans_from_text(page_text)

        local_offset = 0
        for span in spans:
            text = span["text"]
            if not text:
                continue
            start_char = page_start + local_offset
            end_char = start_char + len(text)
            locator = {"type": "pdf", "page": page_number}
            if span.get("bbox"):
                locator["bbox"] = span["bbox"]
            segment = {
                "segment_id": len(segments),
                "start_char": start_char,
                "end_char": end_char,
                "text": text,
                "locator": locator,
            }
            if span.get("font_size"):
                segment["font_size"] = span["font_size"]
            segments.append(segment)
            local_offset += len(text)

        char_parts.append(page_text)
        page_end = page_start + len(page_text)
        pages.append(
            {
                "page": page_number,
                "content": page_text,
                "start_char": page_start,
                "end_char": page_end,
                "locator": {"type": "pdf", "page": page_number},
            }
        )

    char_stream = "".join(char_parts)
    if doc is not None:
        doc.close()
    return {
        "char_stream": char_stream,
        "total_chars": len(char_stream),
        "pages": pages,
        "segments": segments,
    }


def _normalize_with_offsets(text):
    normalized = []
    offsets = []
    in_space = False
    for index, char in enumerate(text):
        if char.isspace():
            if not in_space:
                normalized.append(" ")
                offsets.append(index)
                in_space = True
        else:
            normalized.append(char.lower())
            offsets.append(index)
            in_space = False
    return "".join(normalized).strip(), offsets


def _find_title_offset(page_text, title):
    if not page_text or not title:
        return 0

    direct = page_text.find(title)
    if direct >= 0:
        return direct

    compact_page, page_offsets = _normalize_with_offsets(page_text)
    compact_title, _ = _normalize_with_offsets(title)
    if not compact_page or not compact_title:
        return 0

    found = compact_page.find(compact_title)
    if found >= 0 and found < len(page_offsets):
        return page_offsets[found]

    title_no_space = re.sub(r"\s+", "", title).lower()
    page_no_space = []
    no_space_offsets = []
    for index, char in enumerate(page_text):
        if not char.isspace():
            page_no_space.append(char.lower())
            no_space_offsets.append(index)
    found = "".join(page_no_space).find(title_no_space)
    if found >= 0 and found < len(no_space_offsets):
        return no_space_offsets[found]
    return 0


def _page_for_index(pages, page_number):
    if not pages:
        return None
    if page_number is None:
        return pages[0]
    page_number = max(1, min(int(page_number), len(pages)))
    return pages[page_number - 1]


def _merge_bboxes(locators):
    boxes = [loc.get("bbox") for loc in locators if loc.get("bbox")]
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _locator_for_range(segments, pages, start_char, end_char):
    overlaps = [
        segment["locator"]
        for segment in segments
        if segment["end_char"] > start_char and segment["start_char"] < end_char
    ]
    if overlaps:
        first_page = overlaps[0]["page"]
        same_page = [loc for loc in overlaps if loc.get("page") == first_page]
        locator = {"type": "pdf", "page": first_page}
        bbox = _merge_bboxes(same_page)
        if bbox:
            locator["bbox"] = bbox
        return locator

    for page in pages:
        if page["start_char"] <= start_char <= page["end_char"]:
            return deepcopy(page["locator"])
    return {"type": "pdf", "page": 1}


def add_char_ranges_to_pdf_structure(structure, char_index):
    """
    Add start_char/end_char/locator to an existing PDF structure tree.

    The legacy start_index/end_index page fields are kept for compatibility.
    start_char is tightened by matching the title inside the start page.
    end_char follows the unified tree semantics: a node covers its full section,
    including children, until the next sibling or the end of its parent range.
    """
    pages = char_index.get("pages", [])
    segments = char_index.get("segments", [])
    char_stream = char_index.get("char_stream", "")
    def add_starts(nodes):
        for node in nodes or []:
            start_page = _page_for_index(pages, node.get("start_index"))
            if start_page:
                title_offset = _find_title_offset(start_page["content"], node.get("title", ""))
                title_start = start_page["start_char"] + title_offset
                page_end = start_page["end_char"]
            else:
                title_start = 0
                page_end = len(char_stream)

            title_end = min(title_start + len(node.get("title", "")), page_end)
            node["start_char"] = title_start
            add_starts(node.get("nodes", []))

    def add_ends(nodes, parent_end):
        sorted_nodes = sorted(nodes or [], key=lambda item: item.get("start_char", 0))
        for index, node in enumerate(sorted_nodes):
            end_char = sorted_nodes[index + 1]["start_char"] if index + 1 < len(sorted_nodes) else parent_end
            node["end_char"] = max(node["start_char"], end_char)
            if node.get("nodes"):
                add_ends(node["nodes"], node["end_char"])
            node["text"] = char_stream[node["start_char"]:node["end_char"]]

    add_starts(structure)
    add_ends(structure, len(char_stream))

    return structure

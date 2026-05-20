import re


CLAUSE_HEADING_RE = re.compile(
    r"^\s*(?:"
    r"\d+(?:\.\d+){0,5}|"
    r"第\s*[一二三四五六七八九十百千万\d]+\s*[条章节款项]|"
    r"[（(]\s*[一二三四五六七八九十\d]+\s*[)）]"
    r")"
)


def _iter_node_clauses(nodes, char_stream):
    for node in nodes or []:
        children = node.get("nodes") or []
        start = node.get("start_char")
        end = node.get("end_char")
        if start is not None and end is not None:
            own_end = min([child.get("start_char", end) for child in children] or [end])
            own_text = char_stream[start:own_end].strip()
            if own_text:
                yield node, own_text, start, own_end
        elif node.get("text"):
            yield node, node["text"].strip(), start, end
        if children:
            yield from _iter_node_clauses(children, char_stream)
        else:
            text = (node.get("text") or "").strip()
            if text and start is None:
                yield node, text, start, end


def split_review_clauses(review_doc, min_chars=20):
    """
    Split the reviewed document into small auditable clauses.

    Priority:
    1. leaf structure nodes from the indexed document
    2. numbered paragraphs inside char_stream
    3. non-empty paragraphs
    """
    clauses = []
    char_stream = review_doc.get("char_stream", "")
    for node, text, start_char, end_char in _iter_node_clauses(review_doc.get("structure", []), char_stream):
        if len(text) >= min_chars:
            clauses.append(
                {
                    "clause_id": f"C{len(clauses) + 1:04d}",
                    "title": node.get("title", ""),
                    "text": text,
                    "start_char": start_char,
                    "end_char": end_char,
                }
            )

    if clauses:
        return clauses

    paragraphs = []
    cursor = 0
    for part in re.split(r"\n\s*\n", char_stream):
        start = char_stream.find(part, cursor)
        if start < 0:
            start = cursor
        end = start + len(part)
        cursor = end
        text = part.strip()
        if text:
            paragraphs.append((start, end, text))

    current = None
    for start, end, text in paragraphs:
        if CLAUSE_HEADING_RE.match(text):
            if current:
                clauses.append(current)
            current = {
                "clause_id": f"C{len(clauses) + 1:04d}",
                "title": text.splitlines()[0][:80],
                "text": text,
                "start_char": start,
                "end_char": end,
            }
        elif current:
            current["text"] += "\n\n" + text
            current["end_char"] = end
        elif len(text) >= min_chars:
            clauses.append(
                {
                    "clause_id": f"C{len(clauses) + 1:04d}",
                    "title": text.splitlines()[0][:80],
                    "text": text,
                    "start_char": start,
                    "end_char": end,
                }
            )
    if current:
        clauses.append(current)

    return clauses

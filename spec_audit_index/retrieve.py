import json


def _remove_fields(data, fields):
    if isinstance(data, dict):
        return {k: _remove_fields(v, fields) for k, v in data.items() if k not in fields}
    if isinstance(data, list):
        return [_remove_fields(item, fields) for item in data]
    return data


def get_document(documents, doc_id):
    doc = documents.get(doc_id)
    if not doc:
        return json.dumps({"error": f"Document {doc_id} not found"}, ensure_ascii=False)
    result = {
        "doc_id": doc_id,
        "doc_name": doc.get("doc_name", ""),
        "doc_description": doc.get("doc_description", ""),
        "type": doc.get("type", ""),
        "status": "completed",
        "total_chars": doc.get("total_chars", 0),
    }
    if doc.get("type") == "pdf":
        result["page_count"] = doc.get("page_count", 0)
    elif doc.get("type") == "md":
        result["line_count"] = doc.get("line_count", 0)
    elif doc.get("type") == "xlsx":
        result["sheet_count"] = len(doc.get("structure", []))
    elif doc.get("type") == "docx":
        result["paragraph_count"] = len(doc.get("segments", []))
    return json.dumps(result, ensure_ascii=False)


def get_document_structure(documents, doc_id):
    doc = documents.get(doc_id)
    if not doc:
        return json.dumps({"error": f"Document {doc_id} not found"}, ensure_ascii=False)
    return json.dumps(_remove_fields(doc.get("structure", []), {"text"}), ensure_ascii=False)


def get_text_by_range(documents, doc_id, start_char, end_char):
    doc = documents.get(doc_id)
    if not doc:
        return json.dumps({"error": f"Document {doc_id} not found"}, ensure_ascii=False)
    try:
        start_char = int(start_char)
        end_char = int(end_char)
    except (TypeError, ValueError):
        return json.dumps({"error": "start_char and end_char must be integers"}, ensure_ascii=False)

    char_stream = doc.get("char_stream")
    if char_stream is None:
        return json.dumps({"error": f"Document {doc_id} does not have char_stream"}, ensure_ascii=False)

    total_chars = len(char_stream)
    start_char = max(0, min(start_char, total_chars))
    end_char = max(start_char, min(end_char, total_chars))

    locators = []
    seen = set()
    for segment in doc.get("segments", []):
        if segment.get("end_char", 0) <= start_char or segment.get("start_char", 0) >= end_char:
            continue
        locator = segment.get("locator") or {}
        key = json.dumps(locator, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key)
            locators.append(locator)

    return json.dumps(
        {
            "text": char_stream[start_char:end_char],
            "start_char": start_char,
            "end_char": end_char,
            "locators": locators,
        },
        ensure_ascii=False,
    )


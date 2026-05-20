import json
import os
import uuid
from pathlib import Path

from .excel_char_index import build_xlsx_document
from .md_char_index import build_md_document
from .pdf_char_index import build_pdf_char_index
from .pdf_heuristic_index import build_pdf_heuristic_structure
from .retrieve import get_document, get_document_structure, get_text_by_range
from .word_char_index import build_docx_document


META_INDEX = "_meta.json"


class SpecAuditIndexClient:
    """Lightweight local document index for spec audit."""

    def __init__(self, workspace):
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.documents = {}
        self._load_workspace()

    def index(self, file_path, role="reference"):
        file_path = os.path.abspath(os.path.expanduser(str(file_path)))
        if not os.path.exists(file_path):
            raise FileNotFoundError(file_path)

        doc_id = str(uuid.uuid4())
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            char_index = build_pdf_char_index(file_path, extract_bboxes=True)
            doc = {
                "id": doc_id,
                "type": "pdf",
                "role": role,
                "path": file_path,
                "doc_name": os.path.basename(file_path),
                "doc_description": "",
                "page_count": len(char_index.get("pages", [])),
                "total_chars": char_index["total_chars"],
                "structure": build_pdf_heuristic_structure(char_index),
                "segments": char_index["segments"],
                "char_stream": char_index["char_stream"],
            }
        elif ext == ".docx":
            doc = build_docx_document(file_path, doc_id=doc_id)
            doc["role"] = role
        elif ext == ".xlsx":
            doc = build_xlsx_document(file_path, doc_id=doc_id)
            doc["role"] = role
        elif ext in (".md", ".markdown"):
            doc = build_md_document(file_path, doc_id=doc_id)
            doc["role"] = role
        else:
            raise ValueError(f"Unsupported document format: {file_path}")

        self.documents[doc_id] = doc
        self._save_doc(doc_id)
        return doc_id

    def find_by_path(self, file_path):
        path = os.path.abspath(os.path.expanduser(str(file_path)))
        for doc_id, doc in self.documents.items():
            if doc.get("path") == path:
                return doc_id
        return None

    def ensure_indexed(self, file_path, role="reference"):
        doc_id = self.find_by_path(file_path)
        if doc_id:
            return doc_id
        return self.index(file_path, role=role)

    def reference_doc_ids(self):
        return [doc_id for doc_id, doc in self.documents.items() if doc.get("role") == "reference"]

    def get_document(self, doc_id):
        return get_document(self.documents, doc_id)

    def get_document_structure(self, doc_id):
        return get_document_structure(self.documents, doc_id)

    def get_text_by_range(self, doc_id, start_char, end_char):
        return get_text_by_range(self.documents, doc_id, start_char, end_char)

    def _make_meta_entry(self, doc):
        return {
            "type": doc.get("type", ""),
            "role": doc.get("role", ""),
            "doc_name": doc.get("doc_name", ""),
            "doc_description": doc.get("doc_description", ""),
            "path": doc.get("path", ""),
            "total_chars": doc.get("total_chars", 0),
        }

    def _save_doc(self, doc_id):
        path = self.workspace / f"{doc_id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.documents[doc_id], f, ensure_ascii=False, indent=2)

        meta = self._read_meta()
        meta[doc_id] = self._make_meta_entry(self.documents[doc_id])
        with open(self.workspace / META_INDEX, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    def _read_meta(self):
        path = self.workspace / META_INDEX
        if not path.exists():
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _load_workspace(self):
        meta = self._read_meta()
        for doc_id in meta:
            path = self.workspace / f"{doc_id}.json"
            if not path.exists():
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.documents[doc_id] = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue


import argparse
import asyncio
import concurrent.futures
import json
import os
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None
from agents import Agent, Runner, function_tool, set_tracing_disabled, OpenAIChatCompletionsModel
from openai import AsyncOpenAI

from clause_splitter import split_review_clauses
from spec_audit_index import SpecAuditIndexClient


SYSTEM_PROMPT = """
你是文档合规校核 agent。你的任务是基于给定规范文档，对单条待审核条例进行校核。

你可以看到：
- 单条待审核条例文本
- 可用规范文档列表，包含 doc_id 和文件名

你可以调用工具：
- get_document(doc_id)
- get_document_structure(doc_id)
- get_text_by_range(doc_id, start_char, end_char)
- get_relevant_ranges(query, top_k)

要求：
- 只能依据规范文档工具返回的内容做判断。
- 必须先调用工具查找规范依据，再输出结论。
- 优先使用 get_relevant_ranges 找候选依据，再用 get_text_by_range 读取必要字符范围。
- 不要读取整篇规范文档，优先选择小范围。
- evidence.quote 必须是工具返回文本中的原文片段，不得改写或编造。
- 如果没有找到明确规范依据，verdict 必须为 uncertain。
- 如果待审核条例降低、豁免或违反规范中的强制性要求，verdict 必须为 fail。
- 输出严格 JSON，不要输出多余文本。

输出格式：
{
  "clause_id": "...",
  "verdict": "pass | fail | uncertain",
  "risk_level": "low | medium | high",
  "reason": "简要说明判断依据",
  "evidence": [
    {
      "doc_id": "...",
      "doc_name": "...",
      "start_char": 0,
      "end_char": 100,
      "quote": "引用的规范关键内容"
    }
  ],
  "suggestion": "如不通过或不确定，给出修改建议"
}
"""


DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config(config_path):
    path = Path(config_path)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    if yaml is not None:
        return yaml.safe_load(content) or {}
    return _load_simple_yaml(content)


def _parse_simple_yaml_value(value):
    value = value.strip()
    if value in ("true", "True"):
        return True
    if value in ("false", "False"):
        return False
    if value in ("null", "None", ""):
        return None
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        return value


def _load_simple_yaml(content):
    data = {}
    current_list_key = None
    for raw_line in content.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and current_list_key:
            data[current_list_key].append(_parse_simple_yaml_value(stripped[2:]))
            continue
        current_list_key = None
        if ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value:
            data[key] = _parse_simple_yaml_value(value)
        else:
            data[key] = []
            current_list_key = key
    return data


def _resolve_path(value, base_dir):
    if not value:
        return value
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str((base_dir / path).resolve())


def _resolve_path_list(values, base_dir):
    return [_resolve_path(value, base_dir) for value in values]


def _config_value(config, key, env_names, default=None):
    for env_name in env_names:
        value = os.getenv(env_name)
        if value not in (None, ""):
            return value
    return config.get(key, default)


def _model_name(config):
    return _config_value(config, "model", ["SPEC_AUDIT_MODEL", "OPENAI_MODEL"], "Llama-3.3-70B-Instruct")


def _base_url(config):
    return _config_value(config, "openai_base_url", ["OPENAI_BASE_URL"], "http://127.0.0.1:8888/v1")


def _api_key(config):
    return _config_value(config, "openai_api_key", ["OPENAI_API_KEY"], "EMPTY")


def _get_relevant_ranges(client, reference_doc_ids, query, top_k=5):
    terms = [term for term in str(query).replace("，", " ").replace("。", " ").split() if term]
    if not terms:
        terms = [str(query).strip()] if str(query).strip() else []
    hits = []
    for doc_id in reference_doc_ids:
        doc = client.documents[doc_id]
        for segment in doc.get("segments", []):
            text = segment.get("text", "")
            if not text:
                continue
            score = sum(1 for term in terms if term and term in text)
            if score <= 0:
                continue
            hits.append(
                {
                    "doc_id": doc_id,
                    "doc_name": doc.get("doc_name", ""),
                    "start_char": segment.get("start_char"),
                    "end_char": segment.get("end_char"),
                    "text": text,
                    "locator": segment.get("locator"),
                    "score": score,
                }
            )
    hits.sort(key=lambda item: (-item["score"], item["doc_name"], item["start_char"] or 0))
    return hits[: max(1, int(top_k))]


def _validate_evidence(client, reference_doc_ids, result):
    valid = []
    errors = []
    for item in result.get("evidence") or []:
        doc_id = item.get("doc_id")
        quote = str(item.get("quote") or "").strip()
        try:
            start_char = int(item.get("start_char"))
            end_char = int(item.get("end_char"))
        except (TypeError, ValueError):
            errors.append({"evidence": item, "error": "invalid character range"})
            continue
        if doc_id not in reference_doc_ids:
            errors.append({"evidence": item, "error": "doc_id is not a reference document"})
            continue
        payload = json.loads(client.get_text_by_range(doc_id, start_char, end_char))
        text = payload.get("text", "")
        if not quote or quote not in text:
            errors.append({"evidence": item, "error": "quote is not found in referenced text range"})
            continue
        valid.append(item)

    result["evidence"] = valid
    if errors:
        result["evidence_validation_errors"] = errors
    if not valid:
        result["verdict"] = "uncertain"
        result["risk_level"] = result.get("risk_level") or "medium"
        reason = result.get("reason", "")
        result["reason"] = (reason + "；" if reason else "") + "未找到可校验的规范原文证据。"
        result.setdefault("suggestion", "请补充规范依据后人工复核。")
    return result


async def audit_clause(client, reference_doc_ids, clause, config, verbose=False):
    @function_tool
    def get_document(doc_id: str) -> str:
        """Get metadata for a reference document."""
        if doc_id not in reference_doc_ids:
            return json.dumps({"error": f"{doc_id} is not a reference document"}, ensure_ascii=False)
        return client.get_document(doc_id)

    @function_tool
    def get_document_structure(doc_id: str) -> str:
        """Get the reference document structure without node text."""
        if doc_id not in reference_doc_ids:
            return json.dumps({"error": f"{doc_id} is not a reference document"}, ensure_ascii=False)
        return client.get_document_structure(doc_id)

    @function_tool
    def get_text_by_range(doc_id: str, start_char: int, end_char: int) -> str:
        """Get reference text by precise character range."""
        if doc_id not in reference_doc_ids:
            return json.dumps({"error": f"{doc_id} is not a reference document"}, ensure_ascii=False)
        return client.get_text_by_range(doc_id, start_char, end_char)

    @function_tool
    def get_relevant_ranges(query: str, top_k: int = 0) -> str:
        """Get relevant reference document ranges by keywords for candidate evidence."""
        if not top_k or int(top_k) <= 0:
            top_k = int(config.get("retrieve_top_k", 5))
        return json.dumps(_get_relevant_ranges(client, reference_doc_ids, query, top_k), ensure_ascii=False)

    reference_summary = [
        {
            "doc_id": doc_id,
            "doc_name": client.documents[doc_id].get("doc_name", ""),
            "type": client.documents[doc_id].get("type", ""),
        }
        for doc_id in reference_doc_ids
    ]
    prompt = json.dumps(
        {
            "clause_id": clause["clause_id"],
            "clause_title": clause.get("title", ""),
            "clause_text": clause["text"],
            "reference_documents": reference_summary,
        },
        ensure_ascii=False,
        indent=2,
    )
    agent = Agent(
        name=f"SpecAudit-{clause['clause_id']}",
        instructions=SYSTEM_PROMPT,
        tools=[get_document, get_document_structure, get_text_by_range, get_relevant_ranges],
        model=OpenAIChatCompletionsModel(
            model=_model_name(config),
            openai_client=AsyncOpenAI(
                base_url=_base_url(config),
                api_key=_api_key(config),
            ),
        ),
    )
    result = await Runner.run(agent, prompt)
    output = "" if result.final_output is None else str(result.final_output)
    if verbose:
        print(f"[{clause['clause_id']}] {output}", flush=True)
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        parsed = {
            "clause_id": clause["clause_id"],
            "verdict": "uncertain",
            "risk_level": "medium",
            "reason": "agent did not return valid JSON",
            "raw_output": output,
            "evidence": [],
            "suggestion": "请人工复核该条款。",
        }
    parsed.setdefault("clause_id", clause["clause_id"])
    return _validate_evidence(client, reference_doc_ids, parsed)


async def audit_all(client, reference_doc_ids, clauses, config, concurrency=4, verbose=False):
    semaphore = asyncio.Semaphore(concurrency)

    async def run_one(clause):
        async with semaphore:
            return await audit_clause(client, reference_doc_ids, clause, config, verbose=verbose)

    return await asyncio.gather(*(run_one(clause) for clause in clauses))


def main():
    parser = argparse.ArgumentParser(description="Spec audit agent")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="配置文件路径")
    parser.add_argument("--workspace", help="索引 workspace 目录")
    parser.add_argument("--reference", nargs="+", help="规范文档路径，可传多个")
    parser.add_argument("--review", help="待审核文档路径")
    parser.add_argument("--output", help="审核结果输出路径")
    parser.add_argument("--concurrency", type=int, help="并发审核条例数")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    config_dir = Path(args.config).resolve().parent

    workspace = _resolve_path(args.workspace or config.get("workspace") or str(Path(__file__).parent / "workspace"), config_dir)
    reference_paths = args.reference or config.get("reference_documents")
    review_path = args.review or config.get("review_document")
    output_path = _resolve_path(args.output or config.get("output") or str(Path(__file__).parent / "results" / "audit_result.json"), config_dir)
    concurrency = args.concurrency if args.concurrency is not None else int(config.get("concurrency", 4))
    verbose = args.verbose or bool(config.get("verbose", False))
    if not reference_paths:
        parser.error("请通过 --reference 或 config.yaml 的 reference_documents 指定规范文档")
    if not review_path:
        parser.error("请通过 --review 或 config.yaml 的 review_document 指定待审核文档")
    reference_paths = _resolve_path_list(reference_paths, config_dir)
    review_path = _resolve_path(review_path, config_dir)

    set_tracing_disabled(True)
    client = SpecAuditIndexClient(workspace)

    reference_doc_ids = [client.ensure_indexed(path, role="reference") for path in reference_paths]
    review_doc_id = client.ensure_indexed(review_path, role="review")
    review_doc = client.documents[review_doc_id]
    clauses = split_review_clauses(review_doc)

    print(f"Reference documents: {len(reference_doc_ids)}")
    print(f"Review document: {review_doc.get('doc_name')} ({review_doc_id})")
    print(f"Clauses to audit: {len(clauses)}")

    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            results = pool.submit(
                lambda: asyncio.run(audit_all(client, reference_doc_ids, clauses, config, concurrency, verbose))
            ).result()
    except RuntimeError:
        results = asyncio.run(audit_all(client, reference_doc_ids, clauses, config, concurrency, verbose))

    report = {
        "reference_doc_ids": reference_doc_ids,
        "review_doc_id": review_doc_id,
        "review_doc_name": review_doc.get("doc_name", ""),
        "clause_count": len(clauses),
        "clauses": clauses,
        "results": results,
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"Audit result saved to: {output}")


if __name__ == "__main__":
    main()

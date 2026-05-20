# SpecAudit Agent

文档校核 agent：输入若干规范文档和 1 个待审核文档，逐条拆分待审核条例，并并发调用 agent 查询规范文档进行校核。

## 运行方式

默认连接 OpenAI-compatible 服务：

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8888/v1
export OPENAI_API_KEY=EMPTY
export COMPLIANCE_MODEL=Llama-3.3-70B-Instruct
```

运行：

```bash
python compliance_agent.py \
  --reference /path/to/spec1.pdf /path/to/spec2.docx \
  --review /path/to/review.docx \
  --workspace ./workspace \
  --output ./results/audit_result.json \
  --concurrency 4
```

## 核心流程

1. 规范文档索引到 workspace。
2. 待审核文档索引到 workspace。
3. 待审核文档拆成最小条例。
4. 每条条例并发启动一个 agent。
5. agent 只能通过工具读取规范文档：
   - `get_document(doc_id)`
   - `get_document_structure(doc_id)`
   - `get_text_by_range(doc_id, start_char, end_char)`

## 支持格式

- PDF：PyMuPDF 提取 segments，启发式生成结构，不依赖 PageIndex 原 LLM TOC。
- Word：解析 docx XML，按 Heading 样式构树。
- Excel：解析 xlsx XML，按 sheet/cell 生成结构和 segments。
- Markdown：按 `#` 标题构树。

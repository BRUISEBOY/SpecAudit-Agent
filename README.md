# SpecAudit Agent

文档校核 agent：输入若干规范文档和 1 个待审核文档，逐条拆分待审核条例，并并发调用 agent 查询规范文档进行校核。

## 运行方式

默认读取项目根目录的 `config.yaml`。先启动 OpenAI-compatible 模型服务，例如 vLLM：

```bash
--served-model-name Llama-3.3-70B-Instruct --port 8888
```

运行：

```bash
uv run python spec_audit_agent.py --config ./config.yaml
```

也可以用命令行参数覆盖配置文件中的文档路径和运行参数：

```bash
uv run python spec_audit_agent.py \
  --config ./config.yaml \
  --reference /path/to/spec1.pdf /path/to/spec2.docx \
  --review /path/to/review.docx \
  --output ./results/audit_result.json
```

配置优先级：命令行参数 > 环境变量 > `config.yaml` > 代码默认值。

## 配置文件

`config.yaml` 示例：

```yaml
openai_api_key: "EMPTY"
openai_base_url: "http://127.0.0.1:8888/v1"
model: "Llama-3.3-70B-Instruct"

workspace: "./workspace"
output: "./results/audit_result.json"

reference_documents:
  - "./examples/sample_ref.md"
review_document: "./examples/sample_review.md"

concurrency: 4
retrieve_top_k: 5
verbose: true
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
   - `get_relevant_ranges(query, top_k)`

## 支持格式

- PDF：PyMuPDF 提取 segments，启发式生成结构。
- Word：解析 docx XML，按 Heading 样式构树。
- Excel：解析 xlsx XML，按 sheet/cell 生成结构和 segments。
- Markdown：按 `#` 标题构树。

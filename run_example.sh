#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# OpenAI-compatible model service, for example vLLM:
#   --served-model-name Llama-3.3-70B-Instruct --port 8888
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-http://127.0.0.1:8888/v1}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-EMPTY}"
export COMPLIANCE_MODEL="${COMPLIANCE_MODEL:-Llama-3.3-70B-Instruct}"

# Replace these with your own files.
REFERENCE_DOCS=(
  "./examples/sample_ref.md"
)
REVIEW_DOC="./examples/sample_review.md"

uv run python compliance_agent.py \
  --reference "${REFERENCE_DOCS[@]}" \
  --review "$REVIEW_DOC" \
  --workspace "./workspace" \
  --output "./results/audit_result.json" \
  --concurrency 4 \
  --verbose

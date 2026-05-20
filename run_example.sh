#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

uv run python spec_audit_agent.py \
  --config "./config.yaml"

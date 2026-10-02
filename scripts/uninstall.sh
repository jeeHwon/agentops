#!/usr/bin/env bash
set -euo pipefail

if command -v aops >/dev/null 2>&1; then
  aops _remove-otel-config >/dev/null 2>&1 || true
elif command -v codex-agentops >/dev/null 2>&1; then
  codex-agentops _remove-otel-config >/dev/null 2>&1 || true
fi
codex plugin remove codex-agentops@codex-agentops >/dev/null 2>&1 || true
codex plugin marketplace remove codex-agentops >/dev/null 2>&1 || true
uv tool uninstall codex-agentops >/dev/null 2>&1 || true

echo "Codex AgentOps를 제거했습니다. 관리되는 Codex OTel 블록은 삭제했고 사용자 데이터는 보존했습니다."

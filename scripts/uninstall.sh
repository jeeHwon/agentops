#!/usr/bin/env bash
set -euo pipefail

codex-agentops _remove-otel-config >/dev/null 2>&1 || true
codex plugin remove codex-agentops@codex-agentops >/dev/null 2>&1 || true
codex plugin marketplace remove codex-agentops >/dev/null 2>&1 || true
uv tool uninstall codex-agentops >/dev/null 2>&1 || true

echo "Codex AgentOps was uninstalled. The managed Codex OTel block was removed; user data was preserved."

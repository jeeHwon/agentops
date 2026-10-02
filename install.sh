#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for command_name in codex databricks uv; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "ERROR: required command is missing: $command_name" >&2
    exit 1
  fi
done

codex --version
databricks --version

uv tool install --python 3.11 --force "$REPO_ROOT"

codex plugin remove codex-agentops@codex-agentops >/dev/null 2>&1 || true
codex plugin marketplace remove codex-agentops >/dev/null 2>&1 || true
codex plugin marketplace add "$REPO_ROOT"
codex plugin add codex-agentops@codex-agentops

cat <<'EOF'

Codex AgentOps installed.

1. Run: codex-agentops configure
2. Run: codex-agentops init my-agent
3. Run Codex in that Agent folder and enter /hooks to trust the plugin hooks.
4. Continue using the normal `codex` command.
EOF

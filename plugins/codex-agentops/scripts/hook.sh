#!/bin/sh

if command -v codex-agentops >/dev/null 2>&1; then
  exec codex-agentops _hook
fi

if [ -x "$HOME/.local/bin/codex-agentops" ]; then
  exec "$HOME/.local/bin/codex-agentops" _hook
fi

cat >/dev/null
printf '%s\n' '{"systemMessage":"Codex AgentOps telemetry warning: CLI is not installed; run the repository install script."}'
exit 0

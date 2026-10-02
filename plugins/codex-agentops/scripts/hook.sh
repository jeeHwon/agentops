#!/bin/sh

if command -v aops >/dev/null 2>&1; then
  exec aops _hook
fi

if [ -x "$HOME/.local/bin/aops" ]; then
  exec "$HOME/.local/bin/aops" _hook
fi

if command -v codex-agentops >/dev/null 2>&1; then
  exec codex-agentops _hook
fi

if [ -x "$HOME/.local/bin/codex-agentops" ]; then
  exec "$HOME/.local/bin/codex-agentops" _hook
fi

cat >/dev/null
printf '%s\n' '{"systemMessage":"Codex AgentOps 경고: CLI가 설치되지 않았습니다. 저장소의 install.sh를 실행하세요."}'
exit 0

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

uv tool install --python 3.11 --force --reinstall --refresh "$REPO_ROOT"

codex plugin remove codex-agentops@codex-agentops >/dev/null 2>&1 || true
codex plugin marketplace remove codex-agentops >/dev/null 2>&1 || true
codex plugin marketplace add "$REPO_ROOT"
codex plugin add codex-agentops@codex-agentops

cat <<'EOF'

Codex AgentOps 설치가 완료되었습니다.

1. 설정: aops configure
2. Agent 생성: aops init my-agent
3. Agent 폴더에서 Codex를 실행하고 /hooks에서 Plugin Hook을 검토하고 신뢰합니다.
4. Harness와 Skill 개발: 일반 `codex` 명령을 그대로 사용합니다.
5. UC Skill 게시: aops publish . --profile <profile>
6. Runtime 검증: aops assemble . --profile <profile>
7. Registry 등록: aops register . --version <version> --profile <profile> --registry <catalog.schema>
8. MCP App 배포: aops deploy . --profile <profile> --model-endpoint <endpoint>
EOF

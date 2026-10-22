#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for command_name in databricks uv; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "ERROR: required command is missing: $command_name" >&2
    exit 1
  fi
done

databricks --version

uv tool install --python 3.11 --force --reinstall --refresh "$REPO_ROOT"

if command -v codex >/dev/null 2>&1; then
  codex --version
  codex plugin remove codex-agentops@codex-agentops >/dev/null 2>&1 || true
  codex plugin marketplace remove codex-agentops >/dev/null 2>&1 || true
  codex plugin marketplace add "$REPO_ROOT"
  codex plugin add codex-agentops@codex-agentops
fi

cat <<'EOF'

AgentOps 설치가 완료되었습니다. Code에는 공식 agentbricks CLI가 추가로 필요합니다.

1. 공통 설정 (최초 1회): aops configure
2. 관리자 Registry 초기화 (Registry마다 1회): aops registry-init
3. Agent 생성: aops init my-agent --type skill|code|no-code
4. Skill을 Codex로 실행할 때만: aops configure --codex
   Agent 폴더에서 Codex를 실행하고 /hooks에서 Plugin Hook을 검토하고 신뢰합니다.
5. 구조 검증과 등록: aops validate / aops register --version <version>
6. Registry 조회와 로드: aops list / aops load <agent-id> --version <version>
7. 실행: aops run (Code는 --input, Genie는 --prompt 지정)
EOF

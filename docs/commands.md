# aops 명령어

[README](../README.md) · [개요](overview.md) · [명령어](commands.md) · [프로젝트 폴더](project.md) · [레지스트리·승인](registry.md)

명령은 17개입니다. 개발자가 매일 쓰는 것은 개발 명령 7개이고, 등록·배포는 dbx 이상에서만, `admin`은 관리자만 씁니다. custom 에디션은 모든 명령이 dbx와 같고, 같은 기능을 `aops serve` API로도 씁니다.

**개발 (모든 에디션, 강제 없음)**

| 명령 | 하는 일 | local | dbx | omni |
| --- | --- | :-: | :-: | :-: |
| [`aops init`](#aops-init) | 프로젝트 폴더 만들기 | ● | ● | ● |
| [`aops dev`](#aops-dev) | 개발 화면 (Chat·Batch·Evaluate·기록·메모리) | ● | ● | ● |
| [`aops run`](#aops-run) | 질문 하나, 또는 질문 파일 × 구성 × 반복(배치) | ● | ● | ● |
| [`aops evaluate`](#aops-evaluate) | 채점, 가드레일 시험, 이어가기 시험 | ● | ● | ● |
| [`aops session`](#aops-session) | 세션 보기·하네스 전환·가져오기·내보내기 | ● | ● | ● |
| [`aops memory`](#aops-memory) | 장기 메모리 보기·승인·삭제 | ● | ● | ● |
| [`aops add` · `remove`](#aops-add--remove) | 스킬·도구·MCP 추가·제거 | ● | ● | ● |

**등록·배포 (dbx·omni, 관리자 정책을 코드로 강제)**

| 명령 | 하는 일 | local | dbx | omni |
| --- | --- | :-: | :-: | :-: |
| [`aops configure`](#aops-configure) | Databricks·Omnigent 연결, 에디션 이전 | – | ● | ● |
| [`aops validate`](#aops-validate) | 등록 전 관리자 정책 검사 (강제 시작점) | 참고용 | ● | ● |
| [`aops register`](#aops-register) | 버전 등록 | – | ● | ● |
| [`aops deploy`](#aops-deploy) | 배포 요청 (승인 뒤 배포) | – | ● | ● |
| [`aops promote`](#aops-promote) | 운영 버전 지정 (되돌리기 포함) | – | ● | ● |

**운영·관리**

| 명령 | 하는 일 | local | dbx | omni |
| --- | --- | :-: | :-: | :-: |
| [`aops monitor`](#aops-monitor) | 사용량·예산·비용, 운영 중인 에이전트와 MCP | ● | ● | ● |
| [`aops doctor`](#aops-doctor) | 설치·버전·설정 점검 | ● | ● | ● |
| [`aops harness`](#aops-harness) | 하네스 설치·버전 관리 | ● | ● | ● |
| [`aops admin`](#aops-admin) | 조직 공통 자원 준비, 관리자 정책 | – | ● | ● |
| [`aops serve`](#aops-serve) | aops API 서버 (custom: Custom Front 연결) | – | ● | ● |

## 개발

### `aops init`

```bash
aops init <name> [--type skill|code|no-code] [--harness claude|codex] [--edition local|dbx|omni|custom] [--no-ui]
aops init <name> --type no-code --genie <space-id>
```

- 프로젝트 폴더를 만들고 `uv sync`로 aops와 하네스를 설치합니다.
- 하네스 프로젝트 설정을 만듭니다. 이 폴더에서 `claude`·`codex`를 직접 실행해도 aops 설정과 기록이 적용됩니다. 하네스에서 이 폴더와 hook을 신뢰로 승인해야 하고, 회사 관리 설정이 프로젝트 hook을 막지 않아야 합니다.
- 예시 질문 3개와 기본 가드레일 규칙을 넣고, 끝나면 `aops dev`를 엽니다. `--no-ui`면 열지 않습니다.
- `--type`: 에이전트 종류입니다. `SPEC.md`의 `build_type`에 기록됩니다.
  - `skill`(기본): 지시와 `SKILL.md`. 모든 에디션
  - `code`: Agent Bricks 프로젝트(LangGraph·OpenAI Agents SDK). dbx 이상
  - `no-code`: Genie Agent. dbx 이상. `--genie`가 없으면 워크스페이스의 Genie Agent 목록에서 고르거나 새로 만듭니다(Genie API)
- `--genie <space-id>`: 그 Genie Agent의 정의를 가져와 `genie/agent.json`으로 둡니다. 이후 Genie 화면에서 고친 내용은 `aops dev`가 다시 가져옵니다.
- dbx: Agent Bricks 프로젝트(`agent.toml`, `runtime/`)와 Unity Catalog 이름·Gateway 기본값을 함께 만듭니다.
- omni: 같은 정의로 Omnigent 에이전트 YAML을 만듭니다.

### `aops dev`

```bash
aops dev [--port <port>] [--host 127.0.0.1]
```

- 로컬 개발 화면을 띄웁니다. 탭은 Chat, Batch, Evaluate, 기록, 메모리입니다.
- 가드레일 층별 스위치, 하네스·모델·스킬·도구 선택도 이 화면에서 합니다. 바꾼 값은 `aops.toml`에 저장됩니다.
- dbx: 모델은 Unity Gateway Model services, 도구는 Databricks 제공 MCP·UC MCP service, 스킬은 Unity Gateway Skills, 기록은 MLflow trace에서 고르고 봅니다. 실행 위치로 내 PC 또는 Databricks Sandbox를 고릅니다.
- dbx의 Agent Bricks 프로젝트면 `agentbricks dev`도 함께 띄웁니다. 배포 환경과 같은 에이전트 서버(`DurableAgentServer`, 포트 8000)와 채팅 화면이 열리고, aops는 그 서버를 Batch·Evaluate 비교 대상으로 붙입니다.

### `aops run`

```bash
aops run "<질문>" [--harness claude|codex] [--json]
aops run <questions.yaml> [--variants a,b] [--repeat N] [--concurrency N] [--json]
```

- **질문 하나:** 현재 세션에 이어 붙입니다. 다른 하네스로 실행하면 인계가 일어납니다.
- **질문 파일(배치):** 질문 × 구성 × 반복의 모든 칸을 실행하고 비교표를 냅니다. 칸마다 작업 폴더(git worktree)를 나눠 서로 파일을 덮어쓰지 않습니다.
  - 결과: 칸별 답변, 도구·스킬 사용, 가드레일 판정, 토큰·지연
  - 질문별 도구 사용률: 그 도구를 쓴 실행 수 / 반복 수. 호출 횟수도 따로 셉니다
- 구독 로그인은 한도가 개인 사용 기준이라, 구독 로그인 배치는 기본 동시 실행 수를 낮게 둡니다. 큰 배치는 API 키나 Unity Gateway를 권합니다.
- dbx: 칸 하나가 MLflow trace 하나입니다(`batch_id`·`variant`·`question_id` 태그).
- omni: Omnigent 세션으로 실행합니다.

### `aops evaluate`

```bash
aops evaluate [dataset] [--scorers ...] [--variants a,b]
aops evaluate --suite redteam [--variants a,b] [--target deployed]
aops evaluate --suite handoff
```

- 원할 때 채점합니다. `mlflow.genai.evaluate`를 쓰고 결과는 항상 MLflow에 남깁니다.
- `--suite redteam`: 가드레일 시험입니다. `guardrails/redteam.yaml`의 사례로 층별 차단·통과·오탐을 표로 냅니다. `--variants`로 가드레일을 켠 구성과 끈 구성을 나란히 비교하고, `--target deployed`면 배포된 에이전트를 시험합니다.
- `--suite handoff`: 하네스를 바꿔도 맥락이 이어지는지 시험합니다.
- local: 프로젝트 MLflow(`runs/mlflow.db`)에 남깁니다. 판정 모델은 사용자가 지정합니다. 결과는 개발 화면 Evaluate 탭이나 `mlflow ui`로 봅니다.
- dbx: Databricks MLflow Experiment, UC 평가 데이터셋, Databricks judge를 씁니다. Unity Gateway 정책은 붙여야 할 정책 목록을 점검표로 보여 주고, 시험 질문으로 실제 차단되는지 확인합니다.
- omni: Omnigent 정책(CEL)으로 시험합니다.

### `aops session`

```bash
aops session list
aops session show <id>
aops session switch --harness claude|codex
aops session import --from claude|codex [--session <id>]
aops session export <id>
```

- `list`·`show`: 세션과 구간(하네스·턴 범위), 전환 기록, 인계 내용, 턴별 도구 호출을 보여 줍니다.
- `switch`: 지금 세션을 다른 하네스로 옮깁니다. 같은 하네스·같은 버전으로 돌아가면 그 하네스의 세션을 재개하고, 아니면 인계로 이어 갑니다. 전환 시점은 사용자가 정합니다. aops는 자동으로 전환하지 않습니다.
- `import`: 하네스에 있던 세션 기록을 aops 세션으로 가져옵니다. 사용자가 실행할 때만 가져옵니다.
- `export`: 하네스와 무관한 형식(JSON)으로 내보냅니다.
- 터미널에서 직접 쓴 하네스 세션의 기록은 `[session] capture_terminal`(개발 화면 스위치)로 켜고 끕니다.
- dbx: 세션을 Agent sessions에 저장해 다른 PC나 Databricks Sandbox에서 이어 갑니다.

### `aops memory`

```bash
aops memory list [--pending]
aops memory search "<검색어>"
aops memory accept <id>
aops memory forget <id>
```

- 하네스가 제안한 메모리 변경을 보고 승인(`accept`)하거나 지웁니다(`forget`). 승인한 항목만 장기 메모리가 됩니다(`[memory] review_writes`).
- local: `memory/`에 Markdown으로 저장하고, 검색은 SQLite로 합니다.
- dbx: Agent memory stores에 저장하고, 저장소 단위로 권한을 줍니다.

### `aops add` · `remove`

```bash
aops add skill <path>
aops add tool <name>
aops add mcp <name|url>
aops remove skill|tool|mcp <name>
```

- `skill`: `SKILL.md` 폴더를 `agent/skills/`에 넣고 하네스별 위치에 배치합니다. 형식은 추가할 때 검사합니다.
- `tool`·`mcp`: local은 로컬 MCP 서버를 `agent/tools.toml`에 등록합니다. dbx는 Databricks 제공 MCP(`system.ai.web_search`, `system.ai.dbsql`, 코드 실행 `system.ai.sandbox` 등), UC MCP service, UC function, Genie를 붙이고, Agent Bricks 프로젝트에는 `agentbricks tools add`로 반영합니다.
- 조직에 스킬을 게시하는 일은 `aops register`가 합니다.

## 등록·배포 (dbx·omni)

조직 배포는 항상 아래 순서로만 합니다. 내 PC에서 바로 조직 배포를 하지 않습니다.

1. `aops validate`: 후보 소스 업로드 → Lakeflow Job이 관리자 정책 검사 → `validations`
2. `aops register --version 1.2.0`: 통과한 소스만 등록 → `agent_versions`, UC 모델 버전 (스킬은 스냅샷만)
3. `aops deploy --version 1.2.0`: 배포 요청 → `deploy_requests` (승인 대기)
4. console 화면: 승인 그룹이 [승인]·[반려] → `approvals`
5. 배포 Job: 승인 기록 재확인 → **시험 앱**에 `agentbricks deploy` → `deployments`
6. `aops promote 1.2.0`: 같은 스냅샷을 **운영 앱**에 배포 → 운영 버전 지정 → `production`

### `aops configure`

```bash
aops configure [--edition dbx|omni] [--dry-run]
```

- Databricks CLI 프로필, 카탈로그·스키마·Experiment·Unity Gateway Model service·SQL warehouse를 확인해 `aops.toml [dbx]`에 저장합니다.
- 조직 공통 레지스트리·validate Job·aops 서버가 있는지와 내 권한(읽기·요청·승인)을 확인합니다.
- 프리뷰(Sandbox, Unity Gateway Skills, Agent memory, 서비스 정책 등) 상태를 확인하고, 꺼진 기능은 local 구현을 쓰도록 `[providers]`를 제안합니다.
- **에디션 이전:** local 프로젝트에서 처음 실행하면 데이터를 옮길지 묻습니다. 세션은 Agent sessions로, 메모리는 memory stores로, 평가 기록은 Databricks Experiment로, 평가 데이터는 UC 평가 데이터셋으로 옮깁니다. `--dry-run`은 이전 계획과 옮길 수 없는 항목만 보여 줍니다. 옮기지 않아도 dbx 기능은 쓸 수 있습니다.
- omni: Omnigent에 로그인하고, dbx 프로젝트를 omni로 옮깁니다.

### `aops validate`

```bash
aops validate [--local]
```

강제가 시작되는 첫 단계입니다. 조직 레지스트리에 올릴 후보를 관리자 정책으로 검사합니다.

- 커밋된 소스만 검사합니다. 커밋하지 않은 변경이 있으면 거부합니다.
- 후보 소스와 `uv.lock`을 묶어 UC Volume의 검증 대기 위치에 올리고, validate Lakeflow Job(서버리스)을 실행합니다. 올린 소스로 검사하므로 내 PC 상태와 무관합니다.
- 검사 단계 (항목과 기준은 [관리자 정책](registry.md#관리자-정책)에서 옵니다):
  1. **형식:** `[release]` 필수 항목, `SPEC.md`, `agent.toml`, 스킬 형식, 도구 권한, 허용된 Model service·MCP, 비밀값·금지 파일, 에이전트 서버 종류. 노코드는 Genie 정의의 테이블·지시가 정책에 맞는지
  2. **평가:** `[release] evals` 데이터셋을 채점해 정책의 최소 점수와 비교. 노코드는 Genie benchmark 평가로 채점
  3. **가드레일:** `[release.guardrails]`에 정책이 요구하는 층·규칙이 있는지, 레드팀 통과율
  4. **호환:** `uv.lock`의 하네스·SDK 버전이 허용 범위와 호환표에 있는지
  5. **기록:** MLflow run과 Delta `validations`에 단계별 결과, 소스 해시, 정책 버전을 남김
- 개발 중 기록에 기대지 않습니다. 개발 중 trace나 평가를 했는지는 보지 않고, 필요한 증거는 이 Job이 직접 만듭니다. 개발 중 hook을 꺼 둔 사람도 불리하지 않습니다.
- `--local`: 같은 단계를 내 PC에서 돌립니다. 미리 확인하는 용도라 결과는 참고용이고 승인 근거가 되지 않습니다. local 에디션에는 이 방식만 있습니다.

### `aops register`

```bash
aops register [--version x.y.z]
```

- 같은 소스 해시로 현재 정책에서 통과한 검증이 있어야 등록됩니다.
- 검증 대기 위치의 소스 스냅샷을 버전 위치로 옮기고, UC registered model 버전과 MLflow LoggedModel을 만듭니다. 평가와 trace가 같은 model ID로 이어집니다.
- 스킬은 스냅샷과 해시만 남깁니다. 게시는 승인 뒤 `aops promote` 때 합니다.
- Delta `agents`·`agent_versions`에 소스 해시, git 커밋, 하네스·SDK 버전, UC 모델 버전, 근거 검증을 남깁니다. 버전은 `SPEC.md`의 `agent_version`과 같아야 합니다.
- omni: Omnigent 에이전트로도 등록합니다.

### `aops deploy`

```bash
aops deploy [--version x.y.z] [--target <workspace>]
aops deploy --dev
```

- **요청:** Delta `deploy_requests`에 승인 대기로 남깁니다. 검증할 때의 정책 버전이 지금 정책과 다르면 다시 `validate`하라고 안내합니다.
- **승인:** console 화면에서 승인 그룹(`[dbx] approvers`)이 합니다. 요청자 본인은 승인할 수 없습니다.
- **배포:** 배포 Job(서비스 주체 실행)이 승인 기록을 다시 확인하고 `agentbricks deploy`로 **시험 앱**(`agent-bricks-<이름>-s`)에 배포합니다. 운영 앱(`agent-bricks-<이름>`)은 `promote` 때 바뀝니다. Agent Bricks는 같은 앱에 다시 배포하면 덮어쓰고 버전 전환이 없어서, 운영에 나가기 전에 시험 앱에서 확인합니다.
- **배포 방식:** 등록된 스냅샷(해시 확인)의 사본으로 배포합니다. 등록 때의 `uv.lock` 버전을 함께 고정하고, 배포 뒤 앱에 깔린 버전을 대조합니다. 권한 부여, 앱 응답, 상태 경로까지 확인해야 배포 성공으로 남깁니다.
- **이름:** 앱 이름은 30자까지입니다. `<이름>`은 에이전트 ID에서 만들고, 길면 앞부분과 짧은 해시로 줄입니다.
- **노코드:** 배포 Job이 등록된 Genie 정의를 대상 워크스페이스의 시험 Genie Agent로 만들거나 고칩니다. 운영 Genie Agent는 `promote` 때 바뀝니다. 카탈로그 이름이 다르면 바꿔 넣습니다.
- **에이전트 서버:** AgentKit `DurableAgentServer`(`/api/invocations`)입니다. 요청 ID로 중복 실행을 막고, 동기·스트리밍·백그라운드 호출을 같은 실행으로 다룹니다. Runtime Store(Lakebase)가 실행 상태를 보관해 중단된 실행을 복구합니다.
- **가드레일:** 개발 중 스위치(`[guardrails]`)가 아니라 배포 구성(`[release.guardrails]`)이 배포 패키지에 들어갑니다. Unity Gateway 정책 점검 결과는 `deployments`에 남습니다.
- **운영 기록:** 배포된 에이전트에 MLflow tracing과 Apps telemetry를 켭니다. 정책에 따라 켜지는 조직 서비스의 기록이라, 에이전트를 쓰는 최종 사용자 쪽에서는 끌 수 없습니다.
- **`--dev`:** 승인 없이 본인 앱(`agent-bricks-d<사용자 해시 4자>-<이름>`)으로 배포합니다. 세션·메모리처럼 배포해야만 시험할 수 있는 기능을 위한 개인 시험 배포입니다. 운영 후보로 남지 않고, `promote` 대상이 아니며, console 화면의 운영 목록에 나오지 않습니다.
- omni: Omnigent 관리형 에이전트로 게시하고 정책을 함께 넣습니다.

### `aops promote`

```bash
aops promote <version> [--expected-version <현재 운영 버전>]
```

- 시험 앱 배포가 끝난 버전만 운영으로 지정합니다. 같은 스냅샷을 운영 앱(노코드는 운영 Genie Agent)에 배포하고 확인한 뒤, Delta `production`과 UC 모델 별칭을 한 번에 바꿉니다.
- `--expected-version`: 현재 운영 버전을 확인한 뒤 바꿉니다(동시 변경 방지).
- 되돌리기도 이 명령으로 합니다. 과거에 운영이었던 버전을 지정하면 그 버전의 스냅샷을 운영 앱에 다시 배포합니다.
- 스킬 기반은 이때 그 버전의 스킬을 Unity Gateway Skills에 게시합니다. 사용자는 각자 하네스에서 `ug skills add`로 받아 씁니다. 되돌리면 이전 버전의 스킬을 다시 게시합니다.

## 운영·관리

### `aops monitor`

```bash
aops monitor [--me | --all] [--watch] [--web]
aops monitor dashboard
```

개인 사용자와 관리자가 사용량·예산과 운영 중인 에이전트를 한곳에서 봅니다. 주 화면은 console 화면의 Monitor이고(`--web`), CLI는 같은 데이터를 표로 보여 줍니다(`--watch`로 주기 갱신).

| 보기 | 개인 (`--me`, 기본) | 관리자 (`--all`) |
| --- | --- | --- |
| 사용량 | 내가 부른 요청·토큰·MCP 도구 호출 | 전체·사용자별·에이전트별 사용량 |
| 예산 | 내 예산 소진율 | 예산별 소진율, 차단·알림 기준 근접 |
| 비용 | 내가 만든 앱의 비용 | 앱·Model service별 비용 |
| 에이전트 | 내가 배포한 에이전트의 상태 | 실행 중인 에이전트 전체와 각자 쓰는 MCP |
| 품질·가드레일 | 내 에이전트 평가 점수, 내 기록의 차단 건수 | 배포된 에이전트별 점수, 정책별 차단 건수 |

- 출처: Unity Gateway 사용량(`system.ai_gateway.usage`), 비용(`system.billing.usage`), 감사 로그(`system.access.audit`), Apps telemetry, 예산 API, aops로 배포한 에이전트의 상태 경로(`/aops/status`). 항목마다 데이터 기준 시각을 함께 보여 줍니다. Databricks 문서 기준으로 사용량은 최대 1시간, 비용은 최대 12시간 늦게 반영됩니다.
- 관리자 보기는 등록·배포된 에이전트와 플랫폼 기록만 다룹니다. 개발자의 개발 중 기록은 그 사람이 고른 위치에 남고, aops는 그것을 관리자 보기에 모으지 않습니다.
- `dashboard`: 관리자 현황용 AI/BI 대시보드를 만들고 게시합니다.
- local: aops 이벤트로 하네스별 토큰·세션 사용량만 보여 줍니다. omni: Omnigent 세션 현황을 함께 보여 줍니다.

### `aops doctor`

```bash
aops doctor
aops doctor --fix sync|migrate
```

- 설치 버전과 호환표, 인증, 프리뷰, 프로젝트·hook 신뢰 상태, 하네스 설정을 덮어쓰는 회사 관리 설정, Unity Gateway 주소·토큰이 함께 설정됐는지를 점검합니다.
- 결과는 알림입니다. 자동으로 고치지 않고, 사용자가 일부러 끈 것(hook, 기록, 가드레일)은 문제가 아니라 "꺼짐"으로 표시합니다.
- `--fix sync`: 하네스 설정을 지금 다시 만듭니다. 평소에는 aops 명령이 자동으로 합니다.
- `--fix migrate`: aops 저장 형식을 새 버전으로 옮깁니다. 평소에는 aops를 올린 뒤 처음 실행할 때 백업하고 묻습니다.

### `aops harness`

```bash
aops harness list
aops harness install|use claude|codex[@<version>] [--untested]
aops harness upgrade claude|codex
```

- 호환표의 고정 버전을 uv로 설치·전환합니다. `--untested`로 검증 전 버전도 경고와 함께 쓸 수 있습니다.
- `upgrade`: 호환표의 다음 버전으로 올리고, 녹화된 출력 재생 시험을 통과하지 못하면 되돌립니다.
- dbx: `ug`(Unity Gateway CLI)로 하네스를 Unity Gateway에 연결합니다.

### `aops admin`

```bash
aops admin setup [--apply]
aops admin policy set <policy.toml>
aops admin policy show|diff
```

관리자만 씁니다.

- `setup`: 조직 공통 자원을 번들(`databricks.yml`)로 한 번 배포합니다.
  - 레지스트리 스키마와 테이블
  - validate Job, 배포 Job (서버리스, 서비스 주체 실행)
  - aops 서버(console 화면 + API, Databricks App)와 Apps telemetry 대상 스키마
  - monitor용 뷰
  - 승인 태그 정책 (governed tags)
- custom이면 Custom Front가 쓸 그룹·서비스 주체에도 aops 서버 앱의 `CAN USE`를 줍니다 (앱을 따로 만들지 않습니다).
- 권한 부여는 미리 보여 주고 `--apply`일 때만 실행합니다.
- `policy`: 관리자 정책을 올리고 버전을 관리합니다.

### `aops serve`

```bash
aops serve [--host 127.0.0.1] [--port 8000] [--cors-origin <url>]
```

dbx 기능을 HTTP API 서버로 엽니다. 조직의 웹·서비스가 자기 화면에서 aops를 쓰게 하는 custom 에디션의 서버입니다. 개발 화면과 aops 서버의 console 화면도 같은 서버 코드입니다.

- **API:** REST `/api/v1`과 실행 스트림 SSE(AG-UI 이벤트)입니다. OpenAPI 문서는 `/api/v1/openapi.json`입니다. CLI 명령과 API는 같은 기능입니다.
- **위치:** 조직 서버나 컨테이너에서 이 명령으로 띄웁니다. Databricks App(aops 서버)은 `aops admin setup`이 배포합니다.
- **인증:** Databricks OAuth 토큰(`Authorization: Bearer`)만 받습니다. 사람 권한이 필요한 조회는 그 사람 권한으로, 레지스트리 기록은 서비스 주체로 합니다.
- **실행:** 하네스는 세션마다 Databricks Sandbox에서 돕니다. 프로젝트는 Workspace 폴더(Git 폴더)나 UC Volume에 둡니다.
- 화면 없이 API만 엽니다. 요청·응답 형식은 `/api/v1/docs`에서 보고, 명령 ↔ API 대응은 `GET /api/v1/meta/commands`로 받습니다.
- `--cors-origin`: 다른 도메인의 브라우저 화면이 직접 부를 때 허용할 주소입니다. 조직 서버에서 띄울 때만 씁니다.

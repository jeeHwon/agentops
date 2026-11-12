# aops 명령어

[README](../README.md) · [개요](overview.md) · [명령어](commands.md) · [프로젝트 폴더](project.md) · [레지스트리·승인](registry.md) · [API](api.md)

> **지금** 열: ● 동작 (스킬 기반) · ◐ 일부 동작 · – 설계. 명령마다 **지금:** 줄에 현재 구현을 적었습니다. 코드 기반·노코드는 dbx 에디션의 aops 서버·console 화면에서 동작합니다(각 명령의 **지금:** 줄).

명령은 22개입니다(console 화면의 배포 요청함과 같은 일을 하는 승인 명령 `gates`·`approve`·`reject` 포함). 등록·배포 명령은 [console 화면](console.md)에서도 모두 됩니다. 개발자가 매일 쓰는 것은 개발 명령 7개입니다. 찾기·가져오기와 등록·배포는 dbx 이상에서만 쓰고, `admin`은 관리자만 씁니다. custom 에디션은 모든 명령이 dbx와 같고, 같은 기능을 `aops serve` API로도 씁니다.

**개발 (모든 에디션, 강제 없음)**

| 명령 | 하는 일 | local | dbx | omni | 지금 |
| --- | --- | :-: | :-: | :-: | :-: |
| [`aops init`](#aops-init) | 새 에이전트 만들기 | ● | ● | ● | ◐ |
| [`aops dev`](#aops-dev) | 개발 화면 (Chat·Batch·Evaluate·기록·메모리) | ● | ● | ● | – |
| [`aops run`](#aops-run) | 질문 하나, 또는 질문 파일 × 구성 × 반복(배치) | ● | ● | ● | ◐ |
| [`aops evaluate`](#aops-evaluate) | 채점, 가드레일 시험, 이어가기 시험 | ● | ● | ● | – |
| [`aops session`](#aops-session) | 세션 보기·하네스 전환·가져오기·내보내기 | ● | ● | ● | ◐ |
| [`aops memory`](#aops-memory) | 장기 메모리 보기·승인·삭제 | ● | ● | ● | – |
| [`aops add` · `remove`](#aops-add--remove) | 스킬·도구·MCP 추가·제거 | ● | ● | ● | – |

**찾기·가져오기 (dbx 이상)**

| 명령 | 하는 일 | local | dbx | omni | 지금 |
| --- | --- | :-: | :-: | :-: | :-: |
| [`aops list`](#aops-list) | 등록된 에이전트 목록 | – | ● | ● | ● |
| [`aops load`](#aops-load) | 등록된 에이전트를 가져와 쓰기 | – | ● | ● | ● |

**등록·배포 (dbx·omni, 배포 요청부터 관리자 정책을 코드로 강제)**

| 명령 | 하는 일 | local | dbx | omni | 지금 |
| --- | --- | :-: | :-: | :-: | :-: |
| [`aops configure`](#aops-configure) | Databricks·Omnigent 연결, 에디션 이전 | – | ● | ● | – |
| [`aops register`](#aops-register) | 버전 저장 | – | ● | ● | ● |
| [`aops validate`](#aops-validate) | 관리자 정책 검사 (배포 전 자가 점검) | 참고용 | ● | ● | ● |
| [`aops deploy`](#aops-deploy) | 운영 반영 (검사·승인 뒤), 요청 취소 | – | ● | ● | ◐ |
| [`aops gates` · `approve` · `reject`](#aops-gates--approve--reject) | 배포 요청 보기·승인·반려 (console 화면과 같은 API) | – | ● | ● | ● |
| [`aops promote`](#aops-promote) | 대상자 권한 부여 | – | ● | ● | ● |

**운영·관리**

| 명령 | 하는 일 | local | dbx | omni | 지금 |
| --- | --- | :-: | :-: | :-: | :-: |
| [`aops monitor`](#aops-monitor) | 사용량·예산·비용, 운영 중인 에이전트와 MCP | ● | ● | ● | – |
| [`aops doctor`](#aops-doctor) | 설치·버전·설정 점검 | ● | ● | ● | – |
| [`aops harness`](#aops-harness) | 하네스 설치·버전 관리 | ● | ● | ● | – |
| [`aops admin`](#aops-admin) | 조직 공통 자원 준비, 관리자 정책 | – | ● | ● | ◐ |
| [`aops serve`](#aops-serve) | aops API 서버 (custom: Custom Front 연결) | – | ● | ● | ◐ |

## 개발

### `aops init`

```bash
aops init <name> [--type skill|code|no-code] [--harness claude|codex] [--edition local|dbx|omni|custom] [--no-ui]
```

- `--type`: 에이전트 종류입니다. `SPEC.md`의 `build_type`에 기록됩니다.
  - `skill`(기본): 내 PC에 에이전트 폴더와 스킬 템플릿을 만들고 `uv sync`로 aops와 하네스를 설치합니다. 모든 에디션에서 씁니다.
  - `code`: Agent Bricks 프로젝트(LangGraph·OpenAI Agents SDK)입니다. dbx 이상에서 씁니다. 워크스페이스에서는 Genie Code에 aops 스킬("코드 기반 에이전트 만들기")을 요청하면 템플릿과 aops 설정이 들어옵니다. `aops init --type code`는 같은 템플릿을 내 PC에 만듭니다.
  - `no-code`: Genie Agent입니다. dbx 이상에서 씁니다. aops 서버가 에이전트 폴더를 만들고, 그 폴더에서 Genie Agent를 만들도록 연결합니다. 폴더 권한을 aops가 물려받으므로 따로 공유하지 않아도 검사·배포할 수 있습니다. 이미 만든 Genie Agent는 그 폴더로 옮깁니다.
- 하네스 프로젝트 설정을 만듭니다. 이 폴더에서 `claude`·`codex`를 직접 실행해도 aops 설정과 기록이 적용됩니다. 하네스에서 이 폴더와 hook을 신뢰로 승인해야 하고, 회사 관리 설정이 프로젝트 hook을 막지 않아야 합니다.
- 예시 질문 3개와 기본 가드레일 규칙을 넣고, 끝나면 `aops dev`를 엽니다. `--no-ui`면 열지 않습니다.
- omni: 같은 정의로 Omnigent 에이전트 YAML을 만듭니다.
- **지금:** `--type skill`만 폴더를 만듭니다: `README.md`·`SPEC.md`·`aops.toml`·`agent/instructions.md`·스킬 템플릿 `agent/skills/my-skill/`·`evals/questions.yaml`과 하네스 설정([프로젝트 폴더](project.md)). `uv sync`, 가드레일 규칙, `aops dev` 열기, `--no-ui`는 아직 없습니다. `--type code`는 `agentbricks init` 템플릿에 `aops.toml`·`SPEC.md`(`build_type: code`)를 더합니다(PATH에 `agentbricks` 필요). 노코드는 console [새 에이전트] → 노코드(`POST /api/v1/projects`)에서 만듭니다: SPEC 항목을 받아 aops 서비스 주체가 에이전트 폴더(`/Workspace/aops/no-code/<이름>`)를 만들고 요청자·담당 팀에 `CAN MANAGE`를 준 뒤 `SPEC.md`를 쓰고, 개발용 Genie를 요청자의 토큰으로 그 폴더에 만듭니다(테이블로 새로·기존 스페이스 복사·옮기기). 서비스 주체로 대신 만들지 않습니다(작성자가 못 읽는 테이블의 값이 정의에 들어갈 수 있어서). 레지스트리에는 `agents`·`genie_agents`와 `create` 기록이 남고, 버전은 첫 배포 때 생깁니다. console에서 스킬·코드 기반을 고르면 같은 SPEC 항목이 든 폴더(zip)를 받습니다. CLI `aops init --type no-code`는 console로 안내합니다. 폴더에서 `claude`·`codex`를 직접 실행하면 지시·스킬은 같지만 aops 세션 기록과 trace는 남지 않습니다(hook 없음).

### `aops dev`

```bash
aops dev [--port <port>] [--host 127.0.0.1]
```

- **지금:** 없습니다 (설계).
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
- **지금:** 질문 하나만 실행합니다(`--harness`, `--json`). 내 PC에 설치된 `claude`(Claude Agent SDK)·`codex`(`codex app-server`)를 띄우고, 모델 호출은 각 하네스 설정을 따릅니다. dbx 에디션이고 `[dbx] trace = true`면 턴마다 내 MLflow 실험에 trace 하나를 남깁니다: `agent.turn` 아래 `llm.<모델>`(토큰, MLflow가 계산한 비용), `aops.handoff`, `tool.*`·`tool.skill.<이름>`. Claude는 기본으로 **프로젝트 설정만** 읽습니다(개인 `~/.claude`의 MCP 서버·hook·`CLAUDE.md` 제외, 회사 관리 설정은 적용). `aops.toml [agent] user_settings = true`면 개인 설정도 읽습니다. Codex는 로그인과 모델 경로가 사용자 설정에 있어 사용자 설정을 그대로 씁니다. 배치는 설계입니다.

### `aops evaluate`

```bash
aops evaluate [dataset] [--scorers ...] [--variants a,b]
aops evaluate --suite redteam [--variants a,b] [--target deployed]
aops evaluate --suite handoff
```

- **지금:** 없습니다 (설계).
- 원할 때 채점합니다. `mlflow.genai.evaluate`를 쓰고 결과는 항상 MLflow에 남깁니다.
- `--suite redteam`: 가드레일 시험입니다. `guardrails/redteam.yaml`의 사례로 층별 차단·통과·오탐을 표로 냅니다. `--variants`로 가드레일을 켠 구성과 끈 구성을 나란히 비교하고, `--target deployed`면 배포된 에이전트를 시험합니다.
- `--suite handoff`: 하네스를 바꿔도 맥락이 이어지는지 시험합니다.
- local: 프로젝트 MLflow(`runs/mlflow.db`)에 남깁니다. 판정 모델은 사용자가 지정합니다. 결과는 개발 화면 Evaluate 탭이나 `mlflow ui`로 봅니다.
- dbx: Databricks MLflow Experiment, UC 평가 데이터셋, Databricks judge를 씁니다. Unity Gateway 정책은 붙여야 할 정책 목록을 점검표로 보여 주고, 시험 질문으로 실제 차단되는지 확인합니다.
- omni: Omnigent 정책(CEL)으로 시험합니다.

### `aops session`

```bash
aops session list
aops session show [<id>]
aops session switch --harness claude|codex
aops session new [--harness claude|codex]
aops session import --from claude|codex [--session <id>]
aops session export <id>
```

- `list`·`show`: 세션과 구간(하네스·턴 범위), 전환 기록, 인계 내용, 턴별 도구 호출을 보여 줍니다.
- `switch`: 지금 세션을 다른 하네스로 옮깁니다. 같은 하네스·같은 버전으로 돌아가면 그 하네스의 세션을 재개하고, 아니면 인계로 이어 갑니다. 전환 시점은 사용자가 정합니다. aops는 자동으로 전환하지 않습니다.
- `import`: 하네스에 있던 세션 기록을 aops 세션으로 가져옵니다. 사용자가 실행할 때만 가져옵니다.
- `export`: 하네스와 무관한 형식(JSON)으로 내보냅니다.
- 터미널에서 직접 쓴 하네스 세션의 기록은 `[session] capture_terminal`(개발 화면 스위치)로 켜고 끕니다.
- dbx: 세션을 Agent sessions에 저장해 다른 PC나 Databricks Sandbox에서 이어 갑니다.
- **지금:** `list`·`show`·`switch`·`new`가 동작합니다. 세션은 프로젝트의 `runs/`(SQLite)에 있고, `show`는 MLflow 실험 주소, 구간별 하네스, 턴마다 인계 여부·쓴 도구·스킬·trace ID를 보여 줍니다. `import`·`export`, 터미널 기록, Agent sessions 저장은 설계입니다.

### `aops memory`

```bash
aops memory list [--pending]
aops memory search "<검색어>"
aops memory accept <id>
aops memory forget <id>
```

- **지금:** 없습니다 (설계).
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

- **지금:** 없습니다 (설계). 스킬은 `agent/skills/<이름>/SKILL.md`를 직접 만들면 다음 `aops run`부터 쓰입니다.
- `skill`: `SKILL.md` 폴더를 `agent/skills/`에 넣고 하네스별 위치에 배치합니다. 형식은 추가할 때 검사합니다.
- `tool`·`mcp`: local은 로컬 MCP 서버를 `agent/tools.toml`에 등록합니다. dbx는 Databricks 제공 MCP(`system.ai.web_search`, `system.ai.dbsql`, 코드 실행 `system.ai.sandbox` 등), UC MCP service, UC function, Genie를 붙이고, Agent Bricks 프로젝트에는 `agentbricks tools add`로 반영합니다.
- 조직에 스킬을 올리는 일은 `register`(처음 만들기)와 `deploy`(운영 버전으로 맞추기)가 합니다.

## 찾기·가져오기 (dbx 이상)

### `aops list`

```bash
aops list [--type skill|code|no-code]
```

- 레지스트리에 등록된 에이전트를 유형별로 보여 줍니다(이름, 담당, 운영 버전, 상태).
- 권한이 없는 에이전트도 목록에는 보입니다. 가져오기(`load`)만 막습니다.
- **지금:** 동작합니다. 레지스트리 뷰 `agent_status`를 읽어 에이전트·종류·운영 버전·최신 버전·상태·담당을 보여 줍니다.

### `aops load`

```bash
aops load <agent_id> [--version x.y.z] [--dir <폴더>]
```

- 노코드: 운영용 Genie Agent 링크를 엽니다.
- 코드: 배포된 Databricks App 링크를 엽니다.
- 스킬: aops 서버가 권한을 확인한 뒤 운영 버전을 내줍니다. CLI는 해시를 확인하고 로컬 에이전트 폴더에 풀어, 하네스 설정을 만든 뒤 그 폴더에서 하네스 세션을 엽니다.
  - 권한은 `promote`로 받은 UC Skill 읽기(`READ_VOLUME`)입니다. 서버는 요청한 사람의 권한으로 그 스킬을 읽어 볼 수 있는지로 판정하므로, 그룹으로 받은 권한도 그대로 통합니다.
  - 권한이 없으면 "담당자에게 promote를 요청하세요"를 보여 줍니다.
- `--version`: 운영 버전이 아닌 버전은 담당자·담당 팀만 가져옵니다.
- 가져온 폴더는 사본입니다. 새 운영 버전이 나오면 세션을 열 때 알려 주고, `aops load`로 다시 받습니다. 고친 내용은 `register`로 새 버전을 만듭니다.
- **지금:** 스킬 기반이 동작합니다. 빈 폴더(기본 `./<agent_id>`)에 풀고 해시를 확인한 뒤 하네스 설정을 만듭니다. 하네스 세션은 열지 않으니 그 폴더에서 `aops run`으로 씁니다. 받는 사람의 기록은 그 사람의 MLflow 실험에 남습니다. 코드 기반은 운영 앱 링크, 노코드는 운영용 Genie 링크를 줍니다(console [가져오기]. 실제 사용 권한은 앱 `CAN_USE`·Genie `CAN RUN`이 막음). 새 운영 버전 알림은 설계입니다.

## 등록·배포 (dbx·omni)

조직 배포는 항상 아래 순서로만 합니다. 내 PC에서 바로 조직 배포를 하지 않습니다. 등록·검증·배포 요청은 모두 aops 서버를 거칩니다.

1. `aops register`: 버전 저장 → `agents`·`agent_versions`·`agent_version_skills` (노코드는 5에서 자동)
2. `aops validate`: 등록된 버전을 Lakeflow Job이 관리자 정책으로 검사 → `validation_runs` (배포 요청 때 결과가 없으면 자동으로 돌림)
3. `aops deploy`: 배포 요청 → 검사 결과 확인 → `release_gates`
4. console 화면: 승인 그룹이 [승인]·[반려] → `approvals` (정책에 따라 자동 승인). CLI는 `aops approve`·`aops reject`(같은 API)
5. 배포 Job: 승인 기록 재확인 → 운영 반영 → `deployments`, `agent_production`
6. `aops promote --to <대상>`: 대상자 권한 부여

모든 단계가 `agent_events`에 감사 이력을 남깁니다.

### `aops configure`

```bash
aops configure [--edition dbx|omni] [--dry-run]
```

- **지금:** 없습니다 (설계). `aops.toml [dbx]`의 `profile`·`server`를 직접 적거나 환경변수 `AOPS_PROFILE`·`AOPS_SERVER`를 씁니다.
- Databricks CLI 프로필, 카탈로그·스키마·Experiment·Unity Gateway Model service·SQL warehouse를 확인해 `aops.toml [dbx]`에 저장합니다.
- 조직 공통 레지스트리·validate Job·aops 서버가 있는지와 내 권한(읽기·요청·승인)을 확인합니다.
- 프리뷰(Sandbox, Unity Gateway Skills, Agent memory, 서비스 정책 등) 상태를 확인하고, 꺼진 기능은 local 구현을 쓰도록 `[providers]`를 제안합니다.
- **에디션 이전:** local 프로젝트에서 처음 실행하면 데이터를 옮길지 묻습니다. 세션은 Agent sessions로, 메모리는 memory stores로, 평가 기록은 Databricks Experiment로, 평가 데이터는 UC 평가 데이터셋으로 옮깁니다. `--dry-run`은 이전 계획과 옮길 수 없는 항목만 보여 줍니다. 옮기지 않아도 dbx 기능은 쓸 수 있습니다.
- omni: Omnigent에 로그인하고, dbx 프로젝트를 omni로 옮깁니다.

### `aops register`

```bash
aops register [--version x.y.z] [--bump fix|minor|major]
```

- 버전을 저장합니다. 처음은 1.0.0이고, 그 뒤에는 `--bump`나 등록 규칙으로 올립니다. 검증 통과를 요구하지 않습니다. 강제는 배포 요청에서 합니다.
- 에이전트마다 UC Volume을 하나 두고, 버전마다 `<version>.zip`을 한 번만 씁니다. 등록된 버전은 바꿀 수 없습니다.
- 스킬: 등록은 볼륨 zip과 스킬 해시만 남깁니다. UC Skill(Unity Gateway Skills)은 **처음 배포할 때** 배포 Job이 만들고, 이때는 아무에게도 읽기 권한을 주지 않습니다. UC Skill의 내용은 늘 운영 버전이고, 새 버전의 내용은 `deploy` 때 바꿉니다. 배포되지 않은 이름(템플릿, 이름을 바꾼 스킬)은 UC에 생기지 않습니다.
- 코드: UC registered model 버전과 MLflow LoggedModel도 만듭니다. 평가와 trace가 같은 model ID로 이어집니다.
- 노코드: 따로 실행하지 않습니다. 배포에 성공하면 배포 Job이 그 정의로 버전을 등록합니다.
- Delta `agents`·`agent_versions`에 버전 해시, 하네스·SDK 버전, UC 모델 버전, SPEC 정보를 남깁니다.
- omni: Omnigent 에이전트로도 등록합니다.
- Git 사본 (선택): 정책에 `git`이 있으면 버전을 에이전트 저장소(`<접두어><agent_id>`, 비공개)의 `main`에 커밋하고 `v<버전>` 태그를 답니다. 커밋 내용은 버전 zip의 파일 그대로입니다. 정본은 계속 볼륨 zip이고, Git은 사람이 보고 비교하는 사본입니다.
  - GitHub는 Unity Catalog HTTP 연결의 프록시로 부릅니다. 토큰은 연결(시크릿)에만 있고, 연결을 쓸 수 있는 주체는 aops 서비스 주체뿐입니다.
  - Git 반영이 실패해도 등록은 성공합니다. 실패는 `agent_version_git`에 남고, 다음 `register`·`deploy` 때 빠진 버전부터 순서대로 다시 올립니다. 비밀값이 있는 버전은 올리지 않습니다(Git 이력에서 지우기 어려움).
  - 배포하면 `production` 브랜치가 운영 버전 커밋으로 옮겨 갑니다(되돌리기도 같음).
- **지금:** 스킬 기반이 동작합니다. 담당자·담당 팀만 새 버전을 등록하고, 마지막 버전과 내용이 같거나 이미 있는 버전 번호면 막습니다. UC Skill 이름은 `<registry>.<agent_id>__<스킬 이름>`이고, 첫 배포 때 aops 서버 서비스 주체가 주인으로 만듭니다. `agent_versions`에는 해시·zip 경로·크기·스킬 해시·기본 하네스·SPEC을 남깁니다(SDK·UC 모델 버전은 설계). 코드 기반은 Agent Bricks 프로젝트(`agent.toml`·`app.yaml`·`pyproject.toml`, SPEC `build_type: code`)를 같은 방식으로 등록하고, 검사는 스킬 대신 `project`(Agent Bricks 형식, `server = agentbricks`)를 봅니다. 배포는 승인 뒤 aops 서버가 서비스 주체로 등록된 zip을 풀어 `agent.toml`의 추적 실험만 운영 위치로 바꾼 사본에서 `agentbricks deploy`를 실행해 운영 앱 `agent-bricks-<이름>`을 바꿉니다(서버리스 Job에서는 `databricks` CLI가 대화형 전용이라 앱 안에서 돌림). `promote`는 앱 `CAN_USE`, `load`는 앱 링크입니다. 노코드는 등록하지 않습니다(501): 배포가 성공할 때 배포 Job이 버전을 매겨 등록합니다.

### `aops validate`

```bash
aops validate [--version x.y.z] [--local]
```

배포 전에 돌려 보는 자가 점검입니다. 배포 요청이 이 결과를 요구하고, 결과가 없으면 배포 요청 때 자동으로 돌립니다.

- 등록된 버전을 검사합니다. 노코드는 배포 요청 때 Genie Agent 정의를 그대로 떠서(스냅샷) 검사합니다.
- validate Lakeflow Job(서버리스, 서비스 주체 실행)이 검사합니다. 등록된 스냅샷으로 검사하므로 내 PC 상태와 무관합니다.
- 검사 단계 (항목과 기준은 [관리자 정책](registry.md#관리자-정책)에서 옵니다):
  1. **형식:** `[release]` 필수 항목, `SPEC.md`, `agent.toml`, 스킬 형식, 도구 권한, 허용된 Model service·MCP, 비밀값·금지 파일, 에이전트 서버 종류. 노코드는 Genie 정의를 작성 규칙(데이터 소스 수, 개인정보 컬럼 숨김, 예제 SQL·벤치마크 수 등)으로 검사합니다
  2. **평가:** `[release] evals` 데이터셋을 채점해 정책의 최소 점수와 비교합니다. 노코드는 Genie 벤치마크로 정확도를 재고, 운영 버전에서 맞던 문항이 틀리는지(회귀) 봅니다
  3. **가드레일:** `[release.guardrails]`에 정책이 요구하는 층·규칙이 있는지, 레드팀 통과율
  4. **호환:** `uv.lock`의 하네스·SDK 버전이 허용 범위와 호환표에 있는지
  5. **기록:** MLflow run과 Delta `validations`에 단계별 결과, 버전 해시, 정책 버전을 남김
- 노코드는 검사하는 동안 Genie Agent 정의가 바뀌면 판정하지 않고 "점검 중 수정됨(`stale`)"으로 남깁니다. 배포는 검사한 스냅샷으로 하므로, 검사하지 않은 수정이 운영에 섞이지 않습니다.
- 개발 중 기록에 기대지 않습니다. 개발 중 trace나 평가를 했는지는 보지 않고, 필요한 증거는 이 Job이 직접 만듭니다. 개발 중 hook을 꺼 둔 사람도 불리하지 않습니다.
- `--local`: 같은 단계를 내 PC에서 돌립니다. 결과는 참고용이고 배포 근거가 되지 않습니다. local 에디션에는 이 방식만 있습니다.
- **지금:** validate Job(`aops-validate`, 서버리스, 서비스 주체 실행)이 스킬 기반을 네 가지로 검사합니다: `format`(SPEC.md 필수 항목과 허용 값), `skills`(Agent Skills 형식, 이름 = 폴더 이름, description 1~1024자), `secrets`(토큰·키 패턴, 금지 파일), `record`(볼륨의 zip이 등록 때 해시와 같은지). `init` 템플릿을 고치지 않은 채(`my-skill`, SPEC·스킬의 템플릿 description)면 `format`·`skills`에서 반려합니다. Job 화면의 설명과 실행 출력에 검사 대상·규칙·실행 코드·검사별 결과가 남고, 결과는 `validation_runs`에 들어갑니다. 노코드는 배포 요청 때 뜬 스냅샷(에이전트 볼륨 `checks/<해시>.zip`: `SPEC.md`·`aops.toml`·`genie/space.json`)을 `format`(SPEC.md, `build_type: no_code`, description 필수)·`definition`(Genie 정의 규칙 15개: ERROR 위반 0건, 준수율이 정책 `no_code.lint_min` 기본 90% 이상. 단계에 규칙별 결과와 고치는 곳)·`secrets`·`integrity`(끝날 때 개발용 정의가 스냅샷과 같은지. 다르면 `stale`로 반려)·`record`로 검사합니다. 통과한 검사는 버전 번호가 아니라 내용 해시로 다시 씁니다. 평가(노코드 벤치마크 정확도·회귀 포함)·가드레일·호환 검사와 `--local`은 설계입니다.

### `aops deploy`

```bash
aops deploy [--version x.y.z] [--reason <이유>] [--target <workspace>]
aops deploy --cancel
aops deploy --dev
```

- **요청:** Delta `deploy_requests`에 남깁니다. 정책의 검증 방식(기본: 이 버전·이 정책으로 통과한 검사 필요)을 확인하고, 통과하지 못하면 승인자에게 가기 전에 반려합니다. 같은 에이전트에 진행 중인 요청이 있으면 받지 않습니다.
- **승인:** console 화면에서 승인 그룹(`[dbx] approvers`)이 합니다. 요청자 본인은 승인할 수 없습니다. 정책으로 자동 승인이나 외부 결재 확장을 고를 수 있습니다.
- **운영 반영:** 배포 Job(서비스 주체 실행)이 승인 기록을 다시 확인하고 내보냅니다.
  - 코드: 해시를 확인한 스냅샷 사본으로 `agentbricks deploy`를 실행해 운영 앱(`agent-bricks-<name>`)을 바꿉니다. `uv.lock`의 정확한 버전으로 설치하고, 배포 뒤 설치된 버전과 앱 응답을 직접 확인합니다.
  - 노코드: 검사한 스냅샷으로 같은 워크스페이스에 운영용 Genie Agent를 만들거나 고칩니다. 작성자는 개발용만 고칩니다. 성공하면 버전을 등록하고 공개 그룹에 실행 권한(`CAN RUN`)을 줍니다.
  - 스킬: 서버로 띄우지 않습니다. UC Skill 내용을 이 버전으로 바꿉니다. 권한을 받은 사람은 다음 실행부터 새 내용을 받습니다.
- **되돌리기:** 이전 버전을 `aops deploy --version <이전 버전>`으로 다시 배포합니다.
- **에이전트 서버:** AgentKit `DurableAgentServer`(`/api/invocations`)입니다. 요청 ID로 중복 실행을 막고, 동기·스트리밍·백그라운드 호출을 같은 실행으로 다룹니다. Runtime Store(Lakebase)가 실행 상태를 보관해 중단된 실행을 복구합니다.
- **가드레일:** 개발 중 스위치(`[guardrails]`)가 아니라 배포 구성(`[release.guardrails]`)이 배포 패키지에 들어갑니다. Unity Gateway 정책 점검 결과는 `deployments`에 남습니다.
- **운영 기록:** 배포된 에이전트에 MLflow tracing과 Apps telemetry를 켭니다. 정책에 따라 켜지는 조직 서비스의 기록이라, 에이전트를 쓰는 최종 사용자 쪽에서는 끌 수 없습니다.
- **`--dev`:** 승인 없이 본인 앱(`agent-bricks-d<사용자 해시>-<name>`)으로 배포합니다. 세션·메모리처럼 배포해야만 시험할 수 있는 기능을 위한 개인 시험 배포입니다. 운영 버전이 되지 않고, `promote` 대상이 아니며, console 화면의 운영 목록에 나오지 않습니다.
- omni: Omnigent 관리형 에이전트로 게시하고 정책을 함께 넣습니다.
- **지금:** 스킬 기반이 동작합니다.
  - 같은 버전을 다시 요청하면 진행 중인 요청을 이어서 보여 주고, 다른 버전의 요청이 진행 중이면 막습니다.
  - 이 버전·이 정책으로 통과한 검사가 없으면 validate Job을 먼저 돌립니다(`exempt`면 건너뜀). 실패하면 `mandatory`는 승인자에게 가기 전에 반려하고, `optional`은 실패 결과를 남긴 채 승인자에게 넘깁니다(승인 방식이 `auto`여도 사람이 판단).
  - 승인: `auto`면 `policy:auto`로 자동 승인합니다. `optional`이면 SPEC의 중요도가 `low`이고 민감도가 `public`·`internal`일 때만 자동 승인하고, 아니면 승인자가 필요합니다(이유가 `decision_reason`에 남음). `required`면 늘 승인자가 console 화면의 배포 요청함이나 [`aops approve`](#aops-gates--approve--reject)로 합니다.
  - 사람이 판단해야 하면 CLI는 gate ID와 승인·반려·취소 명령을 보여 주고 끝납니다. `aops deploy`를 다시 치면 그 요청을 이어서 보여 줍니다.
  - `--cancel`: 요청자나 담당자·담당 팀이 검사 중·승인 대기인 요청을 취소합니다(`gate_canceled`). 운영 반영 뒤에는 이전 버전을 배포해 되돌립니다.
  - 배포 Job(`aops-deploy`, 서비스 주체 실행)이 승인과 zip 해시를 다시 확인하고, 스킬마다 UC Skill을 처음이면 만들고 아니면 내용을 이 버전으로 바꿉니다. 이전 운영 버전에 있던 스킬이 이 버전에 없으면 지우지 않고 Job 출력에 알립니다.
- **지금(노코드):** console [배포 요청]처럼 버전 없이 요청하면 서버가 개발용 정의와 에이전트 폴더의 `SPEC.md`를 스냅샷으로 떠서 제안 버전으로 Gate를 엽니다(처음 1.0.0, 데이터 소스가 바뀌면 major, 그 밖은 minor). 운영과 같은 내용이면 409, 이전 버전과 같은 내용이면 그 버전으로 되돌리기 요청이 됩니다.
  - 승인되면 배포 Job이 스냅샷 해시를 다시 확인하고 운영용 Genie를 `<에이전트 폴더의 상위>/_production/<이름>`(서비스 주체만 쓰기)에 만들거나 정의를 바꿉니다(space ID 유지). 다시 읽은 정의 해시가 스냅샷과 같은지 남기고, 담당자·담당 팀·SPEC의 `consumer_group`에 `CAN RUN`을 준 뒤(실패해도 배포는 그대로, 결과는 `deployments.detail.grants`) 스냅샷을 `<버전>.zip`으로 등록합니다. 버전·운영 포인터·`genie_agents.prod_space_id`·기록이 한 트랜잭션으로 바뀝니다.
  - 운영용 Genie는 서비스 주체가 만들므로 쓰는 테이블에 서비스 주체의 `SELECT`가 있어야 합니다. 되돌리기는 이전 버전의 zip으로 같은 운영용 Genie를 고칩니다.
  - 운영 버전보다 낮은 버전을 `--version`으로 배포하면 되돌리기(`rollback`)로 기록합니다.
  - `--dev`, `--target`, 운영 기록 켜기, 가드레일 패키지는 설계입니다.

### `aops gates` · `approve` · `reject`

```bash
aops gates [--mine | --agent <agent_id>]
aops approve <gate_id> [--comment <의견>]
aops reject <gate_id> --reason <이유>
```

console 화면의 배포 요청·승인과 같은 일을 CLI로 합니다. 모두 aops 서버의 같은 승인 API를 불러서, 어느 화면에서 하든 규칙이 같습니다.

- `gates`: 기본은 내가 승인할 차례인 요청(본인 요청 제외, 승인 그룹일 때)입니다. `--mine`은 내가 낸 요청, `--agent`는 한 에이전트의 요청을 최신 순으로 보여 줍니다. 승인 대기 이유(검사 실패, approval optional의 판단)도 함께 보여 줍니다.
- `approve`: 승인 그룹만 할 수 있고 요청자 본인은 할 수 없습니다. 필요한 승인 수가 차면 배포 Job이 시작됩니다.
- `reject`: 승인과 같은 규칙입니다. 요청자 본인은 반려 대신 `aops deploy --cancel`을 씁니다.
- 승인·반려는 `approvals`와 `agent_events`(`gate_approved`·`gate_rejected`, 결재한 사람)에 남습니다.
- **지금:** 동작합니다. 승인 그룹은 `aops admin setup --approver-group`으로 정하고, 비우면 요청자가 아닌 누구나 승인할 수 있습니다.

### `aops promote`

```bash
aops promote <agent_id> --to group:<그룹> [--to user:<이메일>] [--to sp:<application ID>] [--revoke]
```

- 운영 에이전트를 쓸 대상자에게 권한을 줍니다. `--revoke`면 회수합니다.
  - 노코드: 운영용 Genie Agent의 `CAN RUN`. 공개 그룹에는 배포 때 자동으로 줍니다.
  - 코드: 배포된 앱의 `CAN USE`
  - 스킬: UC Skill의 `READ_VOLUME`. 이 권한이 있어야 `aops load`로 가져옵니다.
- 그룹은 계정 그룹이어야 합니다. 작업 공간 그룹에는 Unity Catalog 권한을 줄 수 없습니다.
- 운영 버전이 있어야 합니다. 요청자는 담당자·담당 팀이어야 하고, 서버가 보안 등급별 공개 범위를 검사합니다.
- 권한은 Databricks 권한(Unity Catalog, 앱·Genie 권한)으로 걸리고 Databricks가 강제합니다. aops는 부여·회수 이력을 남깁니다.
- **지금:** 스킬 기반이 동작합니다(UC Skill `READ_VOLUME` 부여·회수, `agent_events`에 기록). 사용자와 계정 그룹(`account users`) 대상으로 확인했습니다. 담당자·담당 팀이 운영 버전이 있을 때만 할 수 있습니다. 코드 기반은 운영 앱 `CAN_USE`, 노코드는 운영용 Genie `CAN RUN`을 부여·회수합니다(회수는 그 대상만 뺀 목록으로 다시 설정). 보안 등급별 공개 범위 검사는 설계입니다.

## 운영·관리

### `aops monitor`

```bash
aops monitor [--me | --all] [--watch] [--web]
aops monitor dashboard
```

**지금:** 없습니다 (설계). 개발 중 사용량·비용은 MLflow trace(`aops run`)에서 봅니다.

개인 사용자와 관리자가 사용량·예산과 운영 중인 에이전트를 한곳에서 봅니다. 주 화면은 console 화면의 Monitor이고(`--web`), CLI는 같은 데이터를 표로 보여 줍니다(`--watch`로 주기 갱신).

| 보기 | 개인 (`--me`, 기본) | 관리자 (`--all`) |
| --- | --- | --- |
| 사용량 | 내가 부른 요청·토큰·MCP 도구 호출 | 전체·사용자별·에이전트별 사용량 |
| 예산 | 내 예산 소진율 | 예산별 소진율, 차단·알림 기준 근접 |
| 비용 | 내가 만든 앱의 비용 | 앱·Model service별 비용 |
| 에이전트 | 내가 배포한 에이전트의 상태 | 실행 중인 에이전트 전체와 각자 쓰는 MCP |
| 품질·가드레일 | 내 에이전트 평가 점수, 내 기록의 차단 건수 | 배포된 에이전트별 점수, 정책별 차단 건수 |

- 출처: Unity Gateway 사용량(`system.ai_gateway.usage`), 비용(`system.billing.usage`), 감사 로그(`system.access.audit`), 쿼리 기록(`system.query.history`, 노코드), Apps telemetry, 예산 API, aops로 배포한 에이전트의 상태 경로(`/aops/status`). 항목마다 데이터 기준 시각을 함께 보여 줍니다. Databricks 문서 기준으로 사용량은 최대 1시간, 비용은 최대 12시간 늦게 반영됩니다.
- 관리자 보기는 등록·배포된 에이전트와 플랫폼 기록만 다룹니다. 개발자의 개발 중 기록은 그 사람이 고른 위치에 남고, aops는 그것을 관리자 보기에 모으지 않습니다.
- `dashboard`: 관리자 현황용 AI/BI 대시보드를 만들고 게시합니다.
- local: aops 이벤트로 하네스별 토큰·세션 사용량만 보여 줍니다. omni: Omnigent 세션 현황을 함께 보여 줍니다.

### `aops doctor`

```bash
aops doctor
aops doctor --fix sync|migrate
```

- **지금:** 없습니다 (설계).
- 설치 버전과 호환표, 인증, 프리뷰, 프로젝트·hook 신뢰 상태, 하네스 설정을 덮어쓰는 회사 관리 설정, Unity Gateway 주소·토큰이 함께 설정됐는지를 점검합니다.
- dbx: 앱 사용자 권한 범위의 허용 목록, Unity Gateway Skills를 쓸 수 있는지(프리뷰, Compliance Security Profile), 레지스트리 스키마에 넓은 권한이 걸려 있지 않은지도 봅니다.
- 결과는 알림입니다. 자동으로 고치지 않고, 사용자가 일부러 끈 것(hook, 기록, 가드레일)은 문제가 아니라 "꺼짐"으로 표시합니다.
- `--fix sync`: 하네스 설정을 지금 다시 만듭니다. 평소에는 aops 명령이 자동으로 합니다.
- `--fix migrate`: aops 저장 형식을 새 버전으로 옮깁니다. 평소에는 aops를 올린 뒤 처음 실행할 때 백업하고 묻습니다.

### `aops harness`

```bash
aops harness list
aops harness install|use claude|codex[@<version>] [--untested]
aops harness upgrade claude|codex
```

- **지금:** 없습니다 (설계). 하네스는 사용자가 설치한 `claude`·`codex`를 그대로 씁니다.
- 호환표의 고정 버전을 uv로 설치·전환합니다. `--untested`로 검증 전 버전도 경고와 함께 쓸 수 있습니다.
- `upgrade`: 호환표의 다음 버전으로 올리고, 녹화된 출력 재생 시험을 통과하지 못하면 되돌립니다.
- dbx: `ug`(Unity Gateway CLI)로 하네스를 Unity Gateway에 연결합니다.

### `aops admin`

```bash
aops admin setup --profile <프로필> --registry <catalog>.<schema> --warehouse <SQL warehouse ID> \
  [--app aops-server] [--validation-mode mandatory|optional|exempt] [--approval-mode required|optional|auto] \
  [--approver-group <그룹>] [--git-connection <UC 연결> --git-owner <GitHub 조직> [--git-repo-prefix aops-] | --no-git] \
  [--users-group <그룹>] [--genie-skills <스킬 폴더>] [--only tables,policy,dist,app,grants,genie] [--apply]
aops admin policy set <policy.toml>
aops admin policy show|diff
```

관리자만 씁니다.

- `setup`: 조직 공통 자원을 번들(`databricks.yml`)로 한 번 배포합니다.
  - 레지스트리 스키마와 테이블
  - validate Job, 배포 Job (서버리스, 서비스 주체 실행, 자동 재시도 끔)
  - aops 서버(console 화면 + API, Databricks App)와 Apps telemetry 대상 스키마
  - monitor용 뷰, 승인 태그 정책 (governed tags)
  - 권한: 레지스트리 테이블·에이전트 볼륨·UC Skill에 쓰는 것은 aops 서비스 주체만, 사람에게는 읽기만. 노코드 검사·배포를 위해 aops 서비스 주체에 노코드가 쓰는 데이터의 읽기 권한
- custom이면 Custom Front가 쓸 그룹·서비스 주체에도 aops 서버 앱의 `CAN USE`를 줍니다 (앱을 따로 만들지 않습니다).
- 권한 부여는 미리 보여 주고 `--apply`일 때만 실행합니다.
- `policy`: 관리자 정책을 올리고 버전을 관리합니다.
- **지금:** `setup`이 여섯 단계로 동작합니다. 미리보기가 기본이고 `--apply`일 때만 실행합니다.
  1. `tables`: 레지스트리 테이블 14개와 뷰 `agent_status`
  2. `policy`: 옵션의 검사·승인 방식으로 정책 버전을 올림 (`policy` 명령 대신). Git 사본은 `--git-connection`·`--git-owner`로 켜고 `--no-git`으로 끔. git 옵션이 없으면 지금 정책의 git 설정을 그대로 둠
  3. `dist`: Job이 설치할 aops 패키지를 빌드해 볼륨 `aops_dist`에 올림
  4. `app`: aops 서버(Databricks App)를 배포하고 레지스트리·웨어하우스·패키지 경로를 환경변수로 넣음. 코드는 `/Workspace/aops/server/<앱>`(워크스페이스 관리자만 쓰기)에 올리고, 루트에 폴더를 만들 권한이 없으면 실행한 관리자의 홈 폴더(본인과 관리자만 쓰기)에 둡니다. 그 폴더에 쓸 수 있는 주체를 보여 줌. `/Workspace/Shared`는 모든 사용자가 `CAN_MANAGE`를 물려받아 쓰지 않습니다. warehouse를 앱 자원으로 붙일 권한이 없으면 ID만 넘기고, 권한 부여가 실패하면 멈추지 않고 주인에게 부탁할 `GRANT` 문을 보여 줍니다
  5. `grants`: 서버 서비스 주체에 카탈로그·스키마·`aops_dist` 권한, Git 사본이 켜져 있으면 그 연결의 `USE CONNECTION` (사람에게는 사용·조회만). 노코드 에이전트 폴더의 상위 `/Workspace/aops/no-code`를 만들고 서비스 주체에 `CAN_MANAGE`(실패하면 서버는 서비스 주체의 홈 폴더를 씀)
  - `app`은 앱의 사용자 권한 범위에 `files`(스킬 load)·`genie`(노코드 개발용 Genie를 요청자 권한으로)·`workspace.workspace`(요청자 폴더에 템플릿)를 더합니다. 워크스페이스가 모르는 범위는 건너뛰고 알립니다. 범위가 바뀌면 사용자는 화면을 다시 열 때 동의합니다
  - `--users-group`을 주면 그 그룹에 레지스트리 조회(카탈로그·스키마 USE, 스키마 SELECT)를 줍니다. 쓰기 권한은 주지 않습니다
  6. `genie`: Genie Code 워크스페이스 스킬 `aops`(`/Workspace/.assistant/skills/aops/SKILL.md`)와 그 스킬이 쓰는 aops 코드(`/Workspace/aops/genie/src`, 관리자만 쓰기, `--users-group`에 읽기). Genie Code에 "이 에이전트 등록해줘", "배포해줘"라고 하면 노트북에서 PC의 `aops register`·`aops deploy`와 같은 코드가 요청한 사람의 신원으로 서버를 부릅니다. 노트북 토큰은 서버 앱용 토큰으로 바꿔 씁니다. Genie Code의 Auto Mode는 실행 전에 한 번 확인을 받습니다
  - Git 사본에 쓸 연결은 관리자가 먼저 만듭니다: 토큰을 시크릿에 넣고 `CREATE CONNECTION <이름> TYPE HTTP OPTIONS (host 'https://api.github.com', port '443', base_path '/', bearer_token secret('<scope>', '<key>'))`. 토큰은 조직 저장소 전체에 코드(Contents) 쓰기와 저장소 만들기 권한이 필요합니다. GitHub Enterprise Server는 host만 바꿉니다
  - validate·배포 Job은 서버가 처음 쓸 때 서비스 주체로 만들고, 서버의 패키지·설명이 바뀌면 Job 설정을 맞춥니다.
  - 번들 배포, monitor 뷰, 태그 정책, Apps telemetry, `policy` 명령은 설계입니다.

### `aops serve`

```bash
aops serve [--host 127.0.0.1] [--port 8000] [--cors-origin <url>]
```

dbx 기능을 HTTP API 서버로 엽니다. 조직의 웹·서비스가 자기 화면에서 aops를 쓰게 하는 custom 에디션의 서버입니다. 개발 화면과 aops 서버의 console 화면도 같은 서버 코드입니다.

- **API:** REST `/api/v1`과 실행 스트림 SSE(AG-UI 이벤트)입니다. OpenAPI 문서는 `/api/v1/openapi.json`입니다. CLI 명령과 API는 같은 기능입니다.
- **오래 걸리는 일:** 검증·배치·평가는 `202`와 상태를 돌려주고 진행을 스트림으로 흘립니다. 스트림은 주기적으로 신호를 보내고, 끊기면 `Last-Event-ID`로 이어 받습니다.
- **위치:** 조직 서버나 컨테이너에서 이 명령으로 띄웁니다. Databricks App(aops 서버)은 `aops admin setup`이 배포합니다.
- **인증:** Databricks OAuth 토큰(`Authorization: Bearer`)만 받습니다. 사람 권한이 필요한 확인은 그 사람 권한으로, 레지스트리 기록·Job 실행·권한 변경은 서비스 주체로 합니다.
- **실행:** 하네스는 세션마다 Databricks Sandbox에서 돕니다. 프로젝트는 Workspace 폴더(Git 폴더)나 UC Volume에 둡니다.
- `/`는 console 화면입니다([console](console.md)). 요청·응답 형식은 `/api/v1/docs`에서 보고, 명령 ↔ API 대응은 `GET /api/v1/meta/commands`로 받습니다.
- `--cors-origin`: 다른 도메인의 브라우저 화면이 직접 부를 때 허용할 주소입니다. 조직 서버에서 띄울 때만 씁니다.
- **지금:** 동작합니다. 환경변수 `AOPS_REGISTRY`·`AOPS_WAREHOUSE_ID`(Job용 `AOPS_DIST_WHEEL`)가 있으면 스킬·코드 기반·노코드 경로(등록·검사·배포·승인·권한·가져오기, 노코드 만들기·자가 점검, 에이전트·버전·검사·배포·운영 버전·이력 조회, 정책 조회·변경, 내 계정, 새 에이전트 폴더)가 레지스트리에 연결되고, 없으면 `501`입니다([API](api.md)). PC에서 프로필(`AOPS_PROFILE`)로 띄우면 브라우저의 사용자는 그 프로필 사용자입니다. 하네스 실행(대화 경로)과 Sandbox는 아직 없습니다.

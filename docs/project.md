# 프로젝트 폴더와 aops.toml

[README](../README.md) · [개요](overview.md) · [명령어](commands.md) · [프로젝트 폴더](project.md) · [레지스트리·승인](registry.md) · [API](api.md)

## 지금 `aops init`이 만드는 폴더 (스킬 기반)

```text
my-agent/
├── README.md                 [안내] 폴더 구조, 고칠 곳, 쓰는 법
├── SPEC.md                   [고침] 에이전트 설명서 (레지스트리에 들어가는 값)
├── aops.toml                 [고침] aops 설정
├── agent/
│   ├── instructions.md       [고침] 하네스 공통 지시
│   └── skills/
│       └── my-skill/
│           └── SKILL.md      [고침] 스킬 템플릿: 폴더 이름과 내용을 내 스킬로
├── evals/
│   └── questions.yaml        [고침] 평가셋: 질문과 정답(expected)이나 판정 기준(guideline). 배포 전 Gate가 씀
├── .gitignore                runs/, .aops/
├── CLAUDE.md  AGENTS.md      [aops] Claude Code·Codex용 지시 (instructions.md 전체 사본)
├── .claude/skills/           [aops] Claude Code용 스킬 사본
├── .agents/skills/           [aops] Codex용 스킬 사본
├── runs/                     [aops] 세션·턴·원본 이벤트 (SQLite)
└── .aops/                    [aops] 인계 파일, 설정 생성 기록
```

- **[고침]** 사람이 고치는 원본입니다. **[aops]**는 aops가 원본에서 만듭니다.
- `aops run` 직전마다 aops가 원본(`agent/instructions.md`, `agent/skills/`)에서 하네스 설정을 다시 맞춥니다. 사본을 손으로 고치면 덮어쓰지 않고 알려 줍니다.
- `aops register`의 버전 zip에는 원본만 들어갑니다. 하네스 설정(`CLAUDE.md`·`AGENTS.md`·`.claude/`·`.agents/`·`.codex/`)과 기록(`runs/`·`.aops/`)은 빼고, 받는 쪽(`aops load`)에서 다시 만듭니다.
- 스킬은 [Agent Skills](https://agentskills.io) 형식입니다. 폴더 이름이 스킬 이름이고, `SKILL.md`의 `name`은 폴더 이름과 같아야 합니다. 하네스는 `description`을 보고 언제 이 스킬을 쓸지 고릅니다.
- 템플릿(`my-skill`, SPEC·스킬의 템플릿 description)을 그대로 두면 `aops run`은 되지만 셀프 체크(`aops validate`)에서 실패로 나오고 배포 전 평가(Gate)에서 반려됩니다.
- 평가셋 `evals/questions.yaml`은 질문마다 `id`·`text`와 `expected`(정답으로 볼 답)나 `guideline`(판정 기준)을 한 줄씩 씁니다. 배포 전 평가(Gate)가 질문마다 에이전트의 지시·스킬을 받은 모델로 답하고 판정 모델로 채점해, 정확도가 SPEC 중요도별 기준(high 90%·medium 85%·low 80%) 이상이어야 통과합니다. 템플릿 질문만 있으면 반려됩니다.
- 이 폴더에서 `claude`·`codex`를 직접 실행해도 같은 지시와 스킬을 씁니다. 다만 aops 세션 기록과 trace는 `aops run`일 때만 남습니다.
- `aops run`(Claude)은 기본으로 이 폴더의 설정만 읽어서, 개발자 PC가 달라도 같은 에이전트가 같게 동작합니다. 개인 MCP 서버 등을 함께 쓰려면 `[agent] user_settings = true`로 켭니다. 회사 관리 설정(모델 경로 등)은 늘 적용됩니다.

### `SPEC.md`

```yaml
---
agent_id: my-agent
name: my-agent
build_type: skill
owner:                         # 비우면 처음 등록한 사람
owner_group:                   # 담당 팀. 팀원 누구나 새 버전 등록·배포 요청
service_criticality: medium    # low | medium | high
data_sensitivity: internal     # public | internal | confidential | personal
security_level: general
description: 이 에이전트가 하는 일을 한 줄로 적습니다 (에이전트 목록에 보입니다)
---
```

### `aops.toml`

```toml
config_version = 1

[project]
name = "my-agent"
edition = "dbx"                # local | dbx | omni | custom

[agent]
harness = "claude"             # 새 세션의 기본 하네스 (claude | codex). 세션 중 바꾸기: aops session switch
instructions = "agent/instructions.md"
skills = ["agent/skills/*"]    # 이 아래 스킬을 모두 씀
user_settings = false          # true면 aops run(Claude)이 개인 설정(~/.claude의 MCP·hook·CLAUDE.md)도 읽음

[session]
handoff_tokens = { codex = 2000, claude = 4000 }   # 하네스를 바꿀 때 넘길 앞 대화의 크기

[dbx]                          # dbx 에디션일 때만 생김
profile = ""                   # Databricks CLI 프로필. 환경변수 AOPS_PROFILE이 먼저
server = ""                    # aops 서버 주소 (https://<app>.databricksapps.com). 환경변수 AOPS_SERVER가 먼저
trace = true                   # aops run마다 개발 trace를 MLflow 실험에 남김
experiment = ""                # 비우면 /Users/<나>/aops/<이름>
```

## 설계상 더 생길 것

아래는 설계한 전체 구성입니다. 위에 없는 파일·키는 아직 만들지 않습니다.

```text
my-agent/
├── pyproject.toml  uv.lock   aops·하네스·상위 SDK 버전 고정
├── agent/tools.toml          MCP 서버·도구 선언
├── guardrails/
│   ├── rules.toml            2층 규칙
│   └── redteam.yaml          가드레일 시험 사례
├── evals/datasets/           정답이 있는 평가 데이터
├── memory/                   장기 메모리 (Markdown, 승인된 항목만)
├── .codex/                   Codex 프로젝트 설정
├── agent.toml                (코드 기반) Agent Bricks 프로젝트 선언
├── runtime/                  (코드 기반) DurableAgentServer 진입점
└── databricks.yml            (코드 기반) 번들
```

```toml
[agent]
model = "<model-name>"         # dbx에서는 Unity Gateway Model service 이름
tools = "agent/tools.toml"

[guardrails]                   # 개발 중 시험용 스위치. 언제든 끄고 켤 수 있다
instructions = true            # 1층 지시
rules = "log"                  # 2층 규칙: off | log | enforce
gateway = false                # Unity Gateway 서비스 정책 (dbx 이상)
judge = "off"                  # 3층 판정 (Gateway를 쓰지 않을 때)

[session]
capture_terminal = true        # 터미널에서 직접 쓴 하네스 세션도 기록
summarizer = "rules"           # rules | next-harness | <model-name>

[memory]
long_term = true
review_writes = true           # 메모리 변경은 사용자 승인 후 반영
harness_memory = "keep"        # 하네스 자체 메모리: keep | import | off

[[variants]]                   # 배치 비교용 구성. 지정하지 않은 값은 위 설정을 따른다
name = "codex-guard-off"
harness = "codex"
rules = "off"

[release]                      # 등록·배포할 구성
spec = "SPEC.md"
evals = "evals/datasets/main"

[release.guardrails]           # 운영에서 켜 둘 가드레일. 개발 중 [guardrails]와 별개
rules = "enforce"
gateway_policies = ["pii", "jailbreak"]

[dbx]
gateway = true                 # 모델 호출을 Unity Gateway로
```

노코드 에이전트(Genie Agent)는 이 폴더에 정의를 두지 않습니다. Genie 화면에서 만들고 고치며, aops는 배포 요청 때 정의를 스냅샷으로 떠서 평가(정의 규칙·벤치마크)하고 배포합니다.

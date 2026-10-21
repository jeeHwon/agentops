# AgentOps

Markdown으로 Skill-based Agent를 만들고, Unity Catalog에 불변 Release로 등록하며, 기존 `codex`에서 Turn 단위로 MLflow Trace와 평가 결과를 남기는 표준 구현입니다.

```text
Agent 폴더 작성
  → UC Skills 게시
  → Release Artifact를 UC Volume에 저장
  → Release 메타데이터를 Delta Registry에 등록
  → Agent 목록 조회·로드
  → Codex에서 실행
  → 수집된 Turn의 MLflow Trace, 입력·출력이 있는 경우 Scorer 결과 확인
```

Agent 자체를 MCP 서버로 배포하는 기능은 포함하지 않습니다. Web UI나 사내 플랫폼은 `AgentOpsService` Python API를 사용하고, 터미널 사용자는 `aops` CLI를 사용합니다.

추적 범위와 실제 검증 결과는 [MONITORING_STATUS.md](docs/MONITORING_STATUS.md)에 **됨/안됨**으로 정리했습니다. 토큰 전수 추적이나 민감 Skill의 원문 비공개를 보장하지 않습니다.

최신 `SPEC.md` 키·Registry 마이그레이션·운영 검증 사항: [SPEC_CONTRACT.md](docs/SPEC_CONTRACT.md). 등록 버전은 `agent_version`과 동일해야 합니다.

## 설치

Codex CLI, Databricks CLI, Python 3.11 이상과 `uv`가 필요합니다.

```bash
git clone https://github.com/jeeHwon/agentops.git
cd agentops
./install.sh
aops --version
```

`install.sh`는 로컬 Python 도구와 `codex-agentops` Plugin을 Git 저장소에서 설치합니다. Marketplace 게시 과정은 없습니다.

## 최초 설정

Databricks CLI Profile은 사용자가 명시적으로 선택합니다.

```bash
databricks auth login \
  --host https://<workspace-host> \
  --profile <profile>

aops configure \
  --profile <profile> \
  --registry <catalog.schema> \
  --experiment /Shared/agentops
```

`configure`는 다음 작업을 한 번에 수행합니다.

1. 선택한 Profile과 SQL Warehouse 접근을 확인합니다.
2. MLflow Experiment가 없으면 생성하고 테스트 Trace를 기록합니다.
3. `RelevanceToQuery`, `Safety`, `Completeness` Scorer를 **평가용 입력·출력이 모두 있는** `agent.turn`에 한해 100% 샘플링으로 등록합니다. 비동기 assessment 완료 여부는 별도로 확인해야 합니다. 기본 비수집 모드에서는 본문 기반 품질 평가를 하지 않습니다.
4. 사용자 전역 Codex OTel 설정을 로컬 Collector에 연결해 관측된 Token과 Tool 실행시간을 보완합니다. 누락·부분집계는 구분합니다.
5. Profile, Registry, Experiment와 Warehouse를 사용자 설정에 저장합니다.

기존 공유 실험에 서로 다른 scorer 필터가 있으면 이를 자동 덮어쓰지 않고 설정을 중단합니다. 필터 변경 시 태그가 없는 기존 Trace의 Safety 평가 범위가 줄어들 수 있으므로 실험 소유자가 먼저 검토해야 합니다.

이미 다른 환경에서 모니터링 중이고 **Registry 조회·로드만** 별도 환경으로 전환하려면, 모니터링에 영향을 주는 `configure` 대신 다음을 최초 한 번 실행합니다. Databricks Profile은 직접 지정해야 합니다.

```bash
aops use --profile <profile> --registry <catalog.schema> --warehouse-id <warehouse-id>
aops list
aops load <agent-id> --version <version>
```

`use`는 사용자별 조회·로드(`list`, `load`, `catalog-export`) 기본값만 저장하고 MLflow Experiment, Scorer, Codex 전역 설정이나 등록 명령의 대상을 변경하지 않습니다. **게시할 때는 `aops register . --version <version> --profile <profile> --registry <catalog.schema> --warehouse-id <warehouse-id>`로 대상을 명시하세요.** `use`의 선택과 `configure`의 게시 대상이 다르면 인자 없는 `register`/`registry-init`은 잘못된 환경에 쓰지 않도록 중단됩니다. 생략한 Warehouse는 선택한 Profile의 기본 Warehouse를 조회합니다. `load`는 권한·체크섬·Agent 구조를 검증하므로 다운로드 후 다시 `aops validate`할 필요는 없습니다. 다른 Registry로 전환할 때만 `use`를 다시 실행합니다.

관리자는 Registry마다 한 번 초기화합니다. `aops use`로 조회 환경만 바꿨다면 기존 모니터링 설정의 Registry를 잘못 초기화하지 않도록 **쓰기 대상 전체를 명시**합니다.

```bash
aops registry-init --profile <profile> --registry <catalog.schema> --warehouse-id <warehouse-id>
```

## 새 Agent 개발

선택한 모델 공급자/인증이 별도로 준비된 Codex에서 실행합니다. `aops configure`의 OTel 설정은 모델 공급자를 변경하지 않습니다.

```bash
aops init my-agent
cd my-agent
aops validate
codex
```

`SPEC.md`의 `model_service`는 모델 이름만 지정합니다. 실제 Codex 공급자와 인증이 해당 모델에 맞는지는 실행 환경에서 확인해야 합니다.

`aops init`이 만드는 공통 소스는 다음과 같습니다.

```text
my-agent/
├── AGENTS.md
├── README.md
├── SPEC.md
└── skills/
    └── request-structurer/
        ├── SKILL.md
        └── references/
            └── guidelines.md
```

사용자가 수정하는 파일은 위 Markdown 파일뿐입니다.

| 파일 | 작성 내용 |
|---|---|
| `SPEC.md` | Agent ID/Version, 생성·서빙 방식, 출처, Owner, 중요도·민감도·보안등급, UC 권한, 모델 서비스와 성공 기준 |
| `AGENTS.md` | Agent의 행동 원칙, 실행 순서, Skill과 Sub-agent 사용 규칙 |
| `README.md` | Agent 사용 방법과 예시 |
| `skills/<name>/SKILL.md` | 하나의 재사용 가능한 업무 능력과 수행 절차 |
| `skills/<name>/references/*.md` | Skill이 필요할 때 읽는 세부 기준과 참고자료 |
| `agents/<name>/AGENTS.md` | 필요한 경우에만 추가하는 Sub-agent 역할, 위임 조건과 사용할 Skill |

`.agents/`, `.codex/`, `.aops/`는 `aops`가 실행환경에 맞게 생성하므로 직접 수정하지 않습니다.

### 선택적으로 Sub-agent 추가

`aops init`은 Sub-agent를 기본 생성하지 않습니다. Skill만으로 충분하면 `agents/` 폴더가 없어도 `aops validate`와 `aops register`를 사용할 수 있습니다. 독립 검증이 필요할 때만 `agents/validator/AGENTS.md`를 만듭니다.

```markdown
---
name: validator
description: 결과의 누락과 요구사항 충족 여부를 독립적으로 확인할 때 사용합니다.
sandbox_mode: read-only
skills:
  - request-structurer
---

# Validator

결과의 누락과 오류를 확인하고 수정 방향을 제시합니다. 파일은 수정하지 않습니다.
```

`skills`에는 해당 Agent에 실제 존재하는 Skill 폴더명을 적고, 루트 `AGENTS.md`에는 언제 위임할지 명시합니다. Skill 이름을 바꾸면 참조도 함께 바꾸세요. `aops validate`는 선택적으로 작성한 Sub-agent의 형식과 Skill 참조를 검사하며, `.codex/agents/`를 재생성합니다. `sandbox_mode`를 적어도 부모 Codex 세션의 샌드박스·승인 정책을 재정의하지는 못하므로 접근 통제 수단으로 사용하지 마세요.

예전에 내려받은 폴더에 `agents/validator/`가 있다면, 필요 없을 때 그 폴더를 삭제하고 `aops validate`를 다시 실행하세요. 이미 게시한 Release는 불변이므로 변경된 폴더를 등록하려면 `SPEC.md`의 `agent_version`과 `aops register --version`을 함께 새 버전으로 올려야 합니다.

## Skill 작성 규칙

`skills/` 바로 아래 폴더 하나가 UC Skill 하나가 됩니다.

```text
skills/customer-summary/
├── SKILL.md
└── references/
    └── checklist.md
```

```markdown
---
name: customer-summary
description: 고객 자료에서 핵심 사실과 후속 행동을 요약해야 할 때 사용합니다.
---

# Customer Summary

1. 핵심 사실을 확인합니다.
2. 결정 사항과 후속 행동을 구분합니다.
```

- `name`과 `description`은 필수입니다.
- `name`은 폴더명과 같아야 하며 소문자, 숫자와 하이픈만 사용합니다.
- 현재 Skill-based Agent 정의에서는 `SKILL.md`와 `references/*.md`만 허용합니다.
- 실행 코드, 바이너리와 심볼릭 링크는 허용하지 않습니다.
- 기본 Skill은 Agent 전용 UC 이름 `<agent-id>-<skill-name>`으로 게시되므로 다른 Agent의 같은 이름과 충돌하지 않습니다.

기존 조직 공용 UC Skill을 덮어쓰지 않고 참조하려면 `SKILL.md` frontmatter에 다음을 명시합니다.

```yaml
---
name: customer-summary
description: 고객 자료를 정해진 형식으로 요약할 때 사용합니다.
uc_mode: shared-reference
uc_name: <catalog>.<schema>.<existing-skill>
---
```

`shared-reference`는 기존 UC Skill의 접근권한과 존재만 확인하며 내용을 갱신하지 않습니다. Release Artifact에는 등록 시점의 로컬 Skill 스냅샷과 SHA가 함께 고정됩니다.

## Agent 등록과 로드

개발을 마치면 한 명령으로 UC Skills와 Agent Release를 등록합니다.

```bash
aops validate
aops register . --version 1.0.0
```

`register`는 구조 검증, UC Skill 생성 또는 갱신, Release manifest 생성, Artifact 압축·해시, UC Volume 업로드와 Delta Registry 등록을 순서대로 수행합니다. 같은 Agent ID와 버전은 덮어쓰지 않습니다.

`aops list`는 **Skill 원본 Registry**(예: `<catalog>.<skill_schema>`)의 등록 여부를 보여줍니다. 통합 포털의 목록은 별도 Delta Registry(예: `<catalog>.<portal_schema>`)에서 읽으므로 `aops register`만으로 포털에 자동 표시되지 않습니다. 포털에서 **개발 → Skill → 원본 Skill · 포털 연결 대기** 목록의 같은 ID·버전을 선택해 **개발 목록에 연결**하세요. 이는 UC Skill이나 Volume 원본을 다시 게시하는 작업이 아닙니다. 운영 목록에는 별도 Gate 승인과 운영 지정이 필요합니다.

UC Skill 게시에는 Registry 스키마의 관리형 스토리지에서 `/Skills` Files API 쓰기가 실제로 지원되어야 합니다. 게시기는 공식 절차대로 디렉터리를 만들고 파일을 개별 업로드한 뒤 finalize하며, 게시 중 실패하면 **이번 시도에서 생성한** UC Skill만 삭제합니다. 관리형 Volume 생성 성공은 Skill 파일 업로드 성공의 증거가 아닙니다. 어떤 기본 스토리지 위치에서는 Skill 생성 후에도 `/Skills` 쓰기가 `The requested operation is not supported for this skill's storage location`로 거부되었습니다. 같은 워크스페이스에서 별도 클라우드 스토리지에 관리형 위치가 지정된 카탈로그의 전용 스키마를 사용하자 Skill 게시·finalize, Artifact Volume 업로드, Registry 등록·로드가 모두 성공했습니다. 이 관찰을 모든 기본/외부 스토리지의 지원 여부로 일반화하지 말고 대상 스키마에서 실제 파일 업로드를 시험하십시오. [Skill 저장소 요건](https://docs.databricks.com/aws/en/ai-gateway/govern-skills), [게시 절차](https://docs.databricks.com/aws/en/ai-gateway/skill-reference)를 참고하십시오.

```bash
aops list
aops list --all-versions
aops load <agent-id> --version <version>
cd <agent-id>
codex
```

`load`는 다음을 검증한 뒤 로컬 폴더와 Codex Runtime을 만듭니다.

1. UC Volume Artifact 전체 SHA-256
2. Artifact 내부 파일별 SHA-256
3. Registry 메타데이터와 Agent ID·버전
4. 현재 사용자의 각 UC Skill 접근 권한

`aops list`도 기본적으로 현재 사용자가 UC Skill 권한을 가진 Agent만 표시합니다. 관리자가 전체 행을 점검할 때만 `aops list --include-unavailable`을 사용합니다.

Release Artifact에는 등록 시점의 Skill-based Agent 정의, Skill과 Sub-agent 전체 스냅샷이 들어갑니다. 공통 UC Skill이 나중에 바뀌어도 기존 Release 내용은 바뀌지 않습니다.

## Registry 저장 구조

| 저장소 | 저장 내용 |
|---|---|
| Delta Table `agents` | Agent 현재 이름·Owner·중요도·민감도·위험등급·수명주기와 활성 버전 |
| Delta Table `agent_versions` | 불변 버전별 spec snapshot·모델·Artifact·해시·평가 연결·Release manifest |
| Delta Table `agent_version_skills` | Release를 구성하는 UC Skill 이름·해시·게시 방식 |
| Delta Table `release_gates` | 평가·보안·모니터링 Gate와 승인 증거 |
| Delta Table `agent_events` | publish·activate·rollback·deprecate·retire 이력 |
| UC Volume `agent_artifacts` | Skill-based Agent 정의, Skill, Sub-agent와 생성된 Release manifest를 포함한 불변 압축파일 |
| UC Skills | Skill 검색, 권한, 감사와 조직 공용 재사용에 쓰는 현재 Skill 객체 |

`aops registry-init`은 위 Registry 테이블과 Artifact Volume을 멱등하게 준비합니다.

## Codex 실행과 MLflow

Agent 루트에서 평소와 같이 `codex`를 실행합니다. Plugin Hook은 응답이 끝나는 `Stop` 이벤트에서 Turn을 완성하고, Codex OTel과 transcript 보정 결과를 로컬 Outbox에서 결합해 백그라운드로 MLflow에 전송합니다.

**OTel은 프로젝트 `.codex/config.toml`에서 설정하지 않습니다.** Codex는 프로젝트 범위의 `[otel]` 설정을 읽지 않습니다. `aops configure`는 충돌이 없으면 사용자 전역 `~/.codex/config.toml`에 로컬 Collector exporter를 설치합니다. 기존 전역 exporter가 있으면 보존하고 경고하므로 `aops doctor`의 `codex-otel-config`와 실제 Token 수집 여부를 확인하십시오. 기존 exporter를 변경할 수 없으면 해당 Collector로 전달하거나 실행 시 Codex `-c` 설정을 명시해야 합니다.

```text
UserPromptSubmit → 사용자 입력 저장
PostToolUse      → Tool 입력·출력 저장
SubagentStart/Stop → Sub-agent 계층과 결과 저장
Codex OTel       → 관측된 Token·Tool 시간 저장 (결측·부분집계 표시)
Stop             → agent.turn 완성 및 즉시 업로드
SessionEnd       → 누락 Token 사후 보정과 재전송
```

각 `agent.turn`에는 Agent ID·Release, 성공 여부, 지연시간, 모델, 관측된 Token, Tool, Sub-agent와 Skill 체크섬을 기록합니다. 입력·출력 원문은 기본 비수집이며 `aops configure --capture-content`를 명시한 경우에만 패턴 마스킹 후 수집합니다. 100% 모델 호출 커버리지와 완전한 비식별화는 보장하지 않습니다.

```bash
aops doctor --write-test-trace
aops status
aops flush
```

## Web UI와 자체 플랫폼 연동

CLI와 동일한 코어 로직은 `codex_agentops.service.AgentOpsService`로 제공합니다.

`agentops`는 Skill 생성·검증·원본 게시·로드 및 Codex 관측의 정본으로 단독 동작합니다. 별도 `agentops-portal`은 Skill/Code/Genie 통합 목록과 심사 UI를 제공하는 선택적 계층입니다. `aops register`만으로 원본 A안 Registry에 게시되고, 포털 개발 목록까지 관리하려면 그곳에서 **이미 게시된 ID·버전을 연결**합니다. 이 연결은 원본 Skill이나 릴리스를 다시 게시하지 않습니다.

통합 Delta 카탈로그에 A안 릴리스를 연결하려면 현재 사용자가 UC Skill 원문을 읽을 수 있는 릴리스의 메타데이터만 내보내십시오. 이 명령은 Skill 본문이나 Volume 다운로드 주소를 포함하지 않습니다.

```bash
aops catalog-export --all-versions --profile <profile> \
  --registry <catalog.schema> --warehouse-id <warehouse-id>
```

외부 플랫폼에서 사용할 때는 `AgentOpsService.export_catalog(all_versions=True)`가 동일한 공개 메타데이터를 반환합니다. 각 사용자는 여전히 자신의 OAuth 프로필로 `aops load`하여 UC Skill 권한과 릴리스 체크섬을 검증해야 합니다.

```python
from codex_agentops.service import AgentOpsService

service = AgentOpsService()
agents = service.list_agents()
path, release = service.load_agent("my-agent", version="1.0.0", target="./workspace")
```

플랫폼은 사용자별 작업 디렉터리를 만든 뒤 이 API로 Agent를 로드하고 해당 디렉터리에서 `codex` 세션을 실행하면 됩니다.

운영 Web 환경에서는 최종 사용자에게 공용 Artifact Volume 직접 읽기 권한을 주지 않고, 사용자 자격증명 또는 OBO로 UC Skill 권한을 먼저 확인하는 Loader Backend를 통해 Release를 전달해야 합니다.

## 예제와 테스트

두 Skill과 한 Sub-agent가 있는 예제는 [`samples/release-agent`](samples/release-agent)입니다.

```bash
uv sync --dev
uv run pytest
```

## 라이선스

MIT

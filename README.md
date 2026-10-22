# AgentOps

`aops` 하나로 UC Skill 중심 Agent, Agent Bricks CLI 기반 Code Agent, Genie 기반 No-code Agent를 관리합니다. 별도 포털이나 API 서버 없이 공통 `SPEC.md`, 불변 Release, Delta Registry와 MLflow를 사용합니다.

```text
Skill (Codex + UC Skills) ─┐
Code (Agent Bricks CLI) ───┼─ aops ─ SPEC / Release / Registry / MLflow
No-code (Genie API) ───────┘

configure (최초 1회)
    → init → validate → link/deploy → register
     → list/load → run → traces/evaluate → review → promote/rollback
```

세 유형의 실행·배포·평가·심사·운영 절차는 [CLI_LIFECYCLE.md](docs/CLI_LIFECYCLE.md)에 정리했습니다. 환경 설정은 세 유형이 공유하며, 아래 Skill 작성 안내만 Codex에 해당합니다. `register`는 게시, `deploy`는 원격 리소스 변경, `promote`는 운영 카탈로그 버전 지정이며 서로를 암묵적으로 실행하지 않습니다. Agent 자체를 MCP 서버로 배포하지 않습니다.

추적 범위와 실제 검증 결과는 [MONITORING_STATUS.md](docs/MONITORING_STATUS.md)에 **됨/안됨**으로 정리했습니다. 토큰 전수 추적이나 민감 Skill의 원문 비공개를 보장하지 않습니다.

최신 `SPEC.md` 키·Registry 마이그레이션·운영 검증 사항: [SPEC_CONTRACT.md](docs/SPEC_CONTRACT.md). 등록 버전은 `agent_version`과 동일해야 합니다.

## 설치

Databricks CLI, Python 3.11 이상과 `uv`가 필요합니다. Skill 실행에만 Codex CLI와 Hook Plugin이 필요하고, Code에는 공식 `agentbricks` CLI가 필요합니다.

```bash
git clone https://github.com/jeeHwon/agentops.git
cd agentops
./install.sh
aops --version
```

`install.sh`는 로컬 Python 도구를 설치하며 Codex가 있으면 Hook Plugin도 설치합니다. Marketplace 게시 과정은 없습니다. Code의 프레임워크·서버 의존성은 네이티브 Agent Bricks 프로젝트에서 관리합니다.

## 최초 설정

**Skill·Code·Genie 모두 `aops configure` 한 번으로 시작합니다.** Databricks Profile은 사용자가 선택하며 Workspace URL은 해당 Profile에서 확인합니다. 대상 UC Catalog·Schema와 필요한 권한은 관리자가 먼저 준비합니다.

```bash
databricks auth login \
  --host https://<workspace-host> \
  --profile <profile>

aops configure \
  --profile <profile> \
  --registry <catalog.schema> \
  --experiment /Shared/agentops \
  --warehouse-id <warehouse-id>
```

인자 없이 최초 `aops configure`를 실행하면 Profile 선택과 공통 값 입력을 안내합니다. 설정 후에는 사용자가 저장한 Profile과 값을 재사용하고, 환경 변경은 `--profile` 등으로 명시합니다. 비대화형 최초 실행에서는 `--profile`과 `--registry`가 필요합니다. Experiment의 최초 기본값은 `/Shared/agentops`, Warehouse를 생략하면 선택한 Profile의 기본 Warehouse를 조회합니다.

이 명령은 인증을 확인하고, 공통 MLflow Experiment를 Registry와 같은 UC Schema의 Trace 저장소에 연결한 뒤 테스트 Trace와 span을 다시 읽어 확인합니다. Profile·Workspace·Registry·Experiment ID·Warehouse·본문 수집 정책은 사용자 설정 파일 하나에 저장합니다. Codex 설치나 전역 설정 변경은 기본 동작에 포함되지 않습니다. 토큰은 저장하지 않습니다.

이후에는 Profile·Experiment·Warehouse를 반복해서 적지 않습니다.

```bash
aops registry-init                 # 관리자: Registry마다 한 번
aops init my-agent --type code
aops list
aops load <agent-id> --version <version>
```

### UC 스키마와 테이블

아래 이름은 데모 구성 예시이며, 실제 Catalog·Schema는 `configure`와 앱 배포 설정에서 지정합니다.

| 스키마 | 저장하는 것 |
|---|---|
| `<catalog>.agentops_skill_registry` | `aops` 원본: `agents`(신원), `agent_versions`(불변 버전), `agent_version_skills`(Skill 구성), `release_gates`(등록·심사 증빙), `agent_events`(변경 이력), `agent_production`(운영 버전 선택). `agent_artifacts` Volume에 릴리스 파일을 보관하고 UC Skill도 이 스키마에 등록합니다. |
| `<catalog>.agentops` | 별도 통합 포털의 Delta 목록: `agents`(공통 신원), `releases`(Skill·Code·Genie 버전), `production`(포털 운영 버전), `gate_events`(포털 심사 이력). `aops` 단독 사용에는 필요하지 않습니다. |
| `<catalog>.agentops_traces` | 별도 Code 데모의 MLflow UC Trace 저장소. `code_demo_otel_*` 테이블 4개와 `code_demo_trace_*` 뷰 2개는 해당 실험이 관리하며, 모든 `aops` 설치에 필요한 스키마는 아닙니다. 공통 `aops configure`의 Trace 저장소는 위 `aops` Registry 스키마에 연결됩니다. |

`agent_production`과 포털의 `production`은 **서로 자동 동기화되지 않습니다.** 예시 행을 직접 넣어도 `aops review`의 독립 심사나 `aops promote`의 승인 절차를 통과했다는 뜻은 아닙니다.

조회와 쓰기는 같은 설정을 사용합니다. 일회성 `--profile`, `--registry`, `--warehouse-id`는 예외 작업에만 사용합니다. 다른 Workspace로 Profile을 바꾸면 기존 Workspace의 공통 값을 가져오지 않으며, 저장된 Profile의 Workspace URL이 변경되면 재설정을 요구합니다. 같은 Workspace의 다른 인증 Profile은 공통 값을 재사용할 수 있습니다.

같은 Workspace에서 `configure`를 다시 실행하면 생략한 값과 기존 본문 동의를 유지합니다. 본문 수집은 최초 기본 비활성이며 `--capture-content`로 동의하고 `--no-content`로 해제합니다. 환경을 바꾸기 전에 `aops flush`로 기존 대기 이벤트를 처리하십시오. 과거 이벤트의 Trace 바인딩은 바꾸지 않으며 새 공통 저장소로 임의 이전하지 않습니다.

기존 Experiment가 다른 UC 위치에 연결돼 있으면 중단합니다. 기존 저장소의 과거 Trace는 자동 이전하지 않으므로 새 환경에는 새 Experiment 경로를 권장합니다.

### Skill용 Codex 연동

Skill을 Codex로 실행할 때만 공통 설정에 다음 옵션을 추가합니다.

```bash
aops configure --codex
```

기존 공통 값은 유지하면서 Codex 설정을 검사하고 Hook용 로컬 Collector·전역 OTel 연동 및 Turn Scorer를 준비합니다. `RelevanceToQuery`, `Safety`, `Completeness`는 평가용 입력·출력이 있는 `agent.turn`만 100% 샘플링하며 비용이 발생할 수 있습니다. 기존 공유 실험의 Scorer 정책이 다르면 자동 덮어쓰지 않고 중단합니다. `--no-codex`는 Hook 수집과 Codex 진단을 비활성화하며 기존 전역 exporter를 제거하지 않습니다. Code·Genie에는 이 단계가 필요 없습니다.

## 새 Agent 개발

선택한 모델 공급자/인증이 별도로 준비된 Codex에서 실행합니다. `aops configure --codex`의 OTel 설정은 모델 공급자를 변경하지 않습니다.

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

`aops list`는 Skill·Code·Genie를 함께 표시합니다. 별도 카탈로그 연결이나 포털 등록은 필요하지 않습니다. 운영 선택에는 MLflow 평가 증빙과 독립 심사가 필요합니다.

UC Skill 게시에는 Registry 스키마의 관리형 스토리지에서 `/Skills` Files API 쓰기가 실제로 지원되어야 합니다. 게시기는 공식 절차대로 디렉터리를 만들고 파일을 개별 업로드한 뒤 finalize하며, 게시 중 실패하면 **이번 시도에서 생성한** UC Skill만 삭제합니다. 관리형 Volume 생성 성공은 Skill 파일 업로드 성공의 증거가 아닙니다. 어떤 기본 스토리지 위치에서는 Skill 생성 후에도 `/Skills` 쓰기가 `The requested operation is not supported for this skill's storage location`로 거부되었습니다. 같은 워크스페이스에서 별도 클라우드 스토리지에 관리형 위치가 지정된 카탈로그의 전용 스키마를 사용하자 Skill 게시·finalize, Artifact Volume 업로드, Registry 등록·로드가 모두 성공했습니다. 이 관찰을 모든 기본/외부 스토리지의 지원 여부로 일반화하지 말고 대상 스키마에서 실제 파일 업로드를 시험하십시오. [Skill 저장소 요건](https://docs.databricks.com/aws/en/ai-gateway/govern-skills), [게시 절차](https://docs.databricks.com/aws/en/ai-gateway/skill-reference)를 참고하십시오.

```bash
aops list
aops list --all-versions
aops load <agent-id> --version <version>
cd <agent-id>
codex
```

`load`는 다음을 검증한 뒤 로컬 폴더를 복구하며 Skill일 때만 Codex Runtime을 만듭니다.

1. UC Volume Artifact 전체 SHA-256
2. Artifact 내부 파일별 SHA-256
3. Registry 메타데이터와 Agent ID·버전
4. 현재 사용자의 각 UC Skill 또는 연결된 App·Genie 접근 권한

`aops list`도 기본적으로 연결 리소스의 접근을 확인합니다. 리소스가 없는 초안은 Registry·Artifact Volume 권한으로 관리합니다. 관리자가 전체 행을 점검할 때만 `aops list --include-unavailable`을 사용합니다.

Release Artifact에는 등록 시점의 Skill-based Agent 정의, Skill과 Sub-agent 전체 스냅샷이 들어갑니다. 공통 UC Skill이 나중에 바뀌어도 기존 Release 내용은 바뀌지 않습니다.

## Registry 저장 구조

| 저장소 | 저장 내용 |
|---|---|
| Delta Table `agents` | Agent 현재 이름·설계와 최근 등록 버전 (`current_version`; 운영 버전 아님) |
| Delta Table `agent_versions` | 불변 버전별 spec snapshot·모델·Artifact·해시·평가 연결·Release manifest |
| Delta Table `agent_version_skills` | Release를 구성하는 UC Skill 이름·해시·게시 방식 |
| Delta Table `release_gates` | 평가·보안·모니터링 Gate와 승인 증거 |
| Delta Table `agent_events` | publish·activate·rollback·deprecate·retire 이력 |
| Delta Table `agent_production` | 운영 카탈로그 선택 버전·이전 버전·변경 ID |
| UC Volume `agent_artifacts` | 유형별 소스·설정과 Release manifest의 불변 압축파일 |
| UC Skills | Skill 검색, 권한, 감사와 조직 공용 재사용에 쓰는 현재 Skill 객체 |

`aops registry-init`은 위 Registry 테이블과 Artifact Volume을 멱등하게 준비합니다.

## Codex 실행과 MLflow

Agent 루트에서 평소와 같이 `codex`를 실행합니다. Plugin Hook은 응답이 끝나는 `Stop` 이벤트에서 Turn을 완성하고, Codex OTel과 transcript 보정 결과를 로컬 Outbox에서 결합해 백그라운드로 MLflow에 전송합니다.

**OTel은 프로젝트 `.codex/config.toml`에서 설정하지 않습니다.** Codex는 프로젝트 범위의 `[otel]` 설정을 읽지 않습니다. `aops configure --codex`는 충돌이 없으면 사용자 전역 `~/.codex/config.toml`에 로컬 Collector exporter를 설치합니다. 기존 전역 exporter가 있으면 보존하고 경고하므로 `aops doctor`의 `codex-otel-config`와 실제 Token 수집 여부를 확인하십시오. 기존 exporter를 변경할 수 없으면 해당 Collector로 전달하거나 실행 시 Codex `-c` 설정을 명시해야 합니다.

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

## 통합 카탈로그

CLI가 단독으로 목록·버전·실행·평가·심사·운영 선택을 제공합니다. `AgentOpsService`는 로컬 Python facade이며 서버나 UI를 필요로 하지 않습니다. 카탈로그 메타데이터는 다음 명령으로 내보낼 수 있습니다.

```bash
aops catalog-export --all-versions
```

내보내기에는 Skill 본문이나 Artifact 다운로드 URL이 들어가지 않습니다. 실제 실행 권한은 UC·App·Genie ACL로 통제하며 공용 Artifact Volume을 읽을 수 있는 사용자는 소스 스냅샷도 읽을 수 있습니다.

## 예제와 테스트

두 Skill과 한 Sub-agent가 있는 예제는 [`samples/release-agent`](samples/release-agent)입니다.

```bash
python3 scripts/check_uv_lock_public_urls.py
uv sync --dev --frozen
uv run --frozen pytest
```

`uv.lock`은 버전 관리하며 공개 PyPI의 Registry와 Artifact URL만 포함합니다. 테스트도 저장소의 lock 파일을 검사합니다. 일상 실행의 `--frozen`은 환경별 Registry 설정 때문에 lock 파일이 다시 작성되는 것을 방지하며, 의존성 갱신 시에는 별도로 lock을 갱신하고 검사해야 합니다.

승인된 PyPI 미러만 사용할 수 있는 환경에서는 미러 설정을 로컬에 유지합니다. 미러에서 갱신한 lock은 게시 전에 다음과 같이 정규화할 수 있습니다.

```bash
python3 scripts/check_uv_lock_public_urls.py \
  --fix --mirror-url "$APPROVED_PYPI_INDEX"
```

검사·정규화 스크립트는 Python 3.11 이상의 표준 라이브러리만 사용하며 패키지 설치나 네트워크 접속 없이 실행됩니다. 정규화는 명시한 미러의 URL만 공개 주소로 바꾸고 패키지 버전·Artifact 경로·SHA256은 보존하며, 다른 출처나 예상과 다른 경로는 거부합니다. 실제 다운로드 가능 여부는 검증하지 않으므로 네트워크 정책상 허용된 환경에서 별도로 설치를 확인하십시오. 이 정규화는 과거 Git 커밋의 내용을 변경하지 않습니다.

## 라이선스

MIT

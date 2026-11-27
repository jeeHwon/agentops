# 셀프 체크 (`aops validate`) 규칙

> 이 문서는 `scripts/validate_docs.py`가 `aops.validate`의 규칙 목록으로 만듭니다. 손으로 고치지 않습니다.

셀프 체크는 에이전트가 **템플릿대로 잘 구성됐는지** 보는 체크리스트입니다. 언제든 돌리고, 기록하지 않으며, 배포 조건이 아닙니다.
같은 규칙이 내 PC(`aops validate`, 서버 없이), console [셀프 체크], Genie Code에서 돌고, 배포 전 평가(`aops evaluate`)의 첫 단계로 다시 돕니다.

- **ERROR:** aops가 등록·배포할 수 있는 형태가 아닙니다. 배포 전 평가가 반려합니다.
- **WARN:** 막지 않지만 고칠 것 (템플릿 흔적, 평가셋 빠짐, 의존 버전 고정 안 됨 등).

| 종류 | 단계 |
| --- | --- |
| 스킬 | `format` → `config` → `instructions` → `skills` → `evals` → `secrets` |
| 코드 | `format` → `config` → `project` → `secrets` |
| 노코드 | `format` → `definition` → `target` → `secrets` |

## `format` SPEC.md (스킬 · 코드 · 노코드)

| 규칙 | 수준 | 보는 것 | 고치는 곳 |
| --- | --- | --- | --- |
| `spec.present` | ERROR | SPEC.md가 있고 위쪽 --- 사이에 항목이 있다 | SPEC.md (aops init이 만든 형식) |
| `spec.required` | ERROR | agent_id·name·build_type이 비어 있지 않다 | SPEC.md 위쪽 항목 |
| `spec.agent_id` | ERROR | agent_id가 영문 소문자·숫자·하이픈 63자 이하다 | SPEC.md agent_id |
| `spec.build_type` | ERROR | build_type이 이 에이전트의 종류(skill · code · no_code)다 | SPEC.md build_type |
| `spec.values` | ERROR | service_criticality ∈ low·medium·high, data_sensitivity ∈ public·internal·confidential·personal | SPEC.md service_criticality · data_sensitivity |
| `spec.security_level` | ERROR | security_level이 정책의 보안 등급 중 하나다 (기본 general·important·secret) | SPEC.md security_level |
| `spec.description` | ERROR | description이 비어 있지 않고 init 템플릿 문구가 아니다 | SPEC.md description (이 에이전트가 하는 일 한 줄) |
| `spec.body` | WARN | SPEC.md 본문(하는 일·쓰는 사람·주의할 점)을 적었다 | SPEC.md 아래쪽 본문 |
| `spec.approvers` | WARN | approvers(배포 요청을 승인할 사람)가 있다. 없으면 정책의 승인 그룹(없으면 요청자가 아닌 누구나)이 승인한다. 정책이 승인자 지정을 요구하면 ERROR | SPEC.md approvers (이메일이나 group:<그룹>, 쉼표로) |
| `spec.approvers_format` | ERROR | approvers의 값마다 이메일이나 group:<그룹> 형태다 | SPEC.md approvers |
| `spec.approvers_other` | ERROR | approvers에 담당자(owner) 말고 한 명(또는 그룹) 이상 있다 (본인 요청은 본인이 승인할 수 없다) | SPEC.md approvers |
| `spec.approvers_exist` | ERROR | approvers의 사람·그룹이 이 워크스페이스에 있다 (서버에서만 확인) | SPEC.md approvers (워크스페이스 계정·그룹 이름) |
| `spec.target_workspace` | ERROR | target_workspace(운영 워크스페이스)를 적었으면 정책의 운영 워크스페이스 중 하나다 (비우면 정책의 기본 운영 워크스페이스) | SPEC.md target_workspace (aops admin setup --workspace의 이름) |
| `nocode.consumer_group` (노코드) | WARN | 공개 그룹(consumer_group)이 있다. 없으면 운영용 Genie는 담당자·담당 팀만 쓴다 | 에이전트 폴더의 SPEC.md consumer_group |

## `config` aops.toml (스킬 · 코드)

| 규칙 | 수준 | 보는 것 | 고치는 곳 |
| --- | --- | --- | --- |
| `config.present` | ERROR | aops.toml이 있고 TOML로 읽힌다 | aops.toml (aops init이 만든 형식) |
| `config.name` | ERROR | aops.toml [project] name이 SPEC.md agent_id와 같다 | aops.toml [project] name |
| `config.type` | ERROR | aops.toml의 종류가 SPEC.md build_type과 같다 (코드 기반은 [project] type = "code") | aops.toml [project] type |

## `instructions` 지시 (스킬)

| 규칙 | 수준 | 보는 것 | 고치는 곳 |
| --- | --- | --- | --- |
| `instructions.present` | ERROR | 지시 파일(aops.toml [agent] instructions, 기본 agent/instructions.md)이 있고 비어 있지 않다 | agent/instructions.md |
| `instructions.template` | WARN | 지시를 이 에이전트에 맞게 고쳤다 (init 템플릿 그대로가 아니다) | agent/instructions.md |

## `skills` 스킬 (스킬)

| 규칙 | 수준 | 보는 것 | 고치는 곳 |
| --- | --- | --- | --- |
| `skills.present` | ERROR | agent/skills/<이름>/SKILL.md가 하나 이상 있다 | agent/skills/<이름>/SKILL.md |
| `skills.name` | ERROR | 스킬 이름이 소문자·숫자·하이픈 64자 이하(하이픈으로 시작·끝·연속 불가)이고 SKILL.md name = 폴더 이름 | agent/skills/<이름>/ 폴더 이름과 SKILL.md name |
| `skills.description` | ERROR | SKILL.md description이 1~1024자이고 init 템플릿 문구가 아니다 (하네스가 이 설명을 보고 스킬을 고른다) | SKILL.md description |
| `skills.template` | ERROR | init 템플릿 스킬(my-skill)이 남아 있지 않다 | agent/skills/my-skill을 내 스킬 이름으로 |
| `skills.body` | WARN | SKILL.md 본문에 규칙을 적었다 (템플릿 안내문 그대로가 아니다) | SKILL.md 본문 |

## `evals` 평가셋 (스킬)

| 규칙 | 수준 | 보는 것 | 고치는 곳 |
| --- | --- | --- | --- |
| `evals.present` | WARN | 평가셋 evals/questions.yaml이 있다 (배포 전 평가의 품질 섹션이 쓴다) | evals/questions.yaml |
| `evals.questions` | WARN | 질문마다 text와 expected(정답)나 guideline(판정 기준)이 있고 템플릿 질문이 아니다 | evals/questions.yaml |

## `project` Agent Bricks 프로젝트 (코드)

| 규칙 | 수준 | 보는 것 | 고치는 곳 |
| --- | --- | --- | --- |
| `project.files` | ERROR | Agent Bricks 프로젝트 파일 agent.toml·app.yaml·pyproject.toml이 있다 | agentbricks init이 만든 파일 |
| `project.agent_toml` | ERROR | agent.toml [agent] server = agentbricks, framework = langgraph · openai | agent.toml [agent] |
| `project.entry` | WARN | 에이전트 코드 agent/agent.py가 있다 (Agent Bricks 템플릿의 진입점) | agent/agent.py |
| `project.command` | WARN | app.yaml에 앱을 띄우는 command가 있다 | app.yaml command |
| `project.lock` | WARN | uv.lock으로 의존 버전이 고정돼 있다 (없으면 배포 때마다 최신 버전) | uv lock으로 uv.lock 만들기 |
| `project.sample` | WARN | 템플릿 샘플 도구(agent/tools/sample_tool.py)를 지웠거나 내 도구로 바꿨다 | agent/tools/ |

## `definition` Genie 정의 규칙 (노코드)

노코드의 Genie 정의(`genie/space.json`) 규칙입니다. 수준과 값은 정책의 `no_code.rules`로 바꿀 수 있고, ERROR 위반이 없으며 준수율이 정책 기준(`no_code.lint_min`, 기본 90%) 이상이어야 통과합니다.

| 규칙 | 수준 | 보는 것 | 고치는 곳 |
| --- | --- | --- | --- |
| `sources.count` | ERROR | 데이터 소스(테이블·메트릭 뷰)가 1~30개 | Data → 테이블 추가·정리 (Genie 한도 30개) |
| `columns.pii_hidden` | ERROR | 개인정보로 보이는 컬럼(이름·UC 태그)이 숨겨져 있다 | Data → 테이블 → 컬럼 숨김(Hide) |
| `snippets.qualified` | ERROR | SQL 표현식이 테이블.컬럼 형태다 | Instructions → SQL Expressions를 테이블.컬럼 형태로 |
| `examples.count` | ERROR | 예제 질문·SQL이 5개 이상 | Instructions → SQL Queries에 예제 질문·SQL 추가 |
| `text.single` | ERROR | Text Instruction이 하나다 | Instructions → Text는 하나로 합치기 |
| `benchmarks.min` | ERROR | 벤치마크(질문·정답 SQL)가 5개 이상 | Benchmarks → Add benchmark |
| `snippets.limit` | ERROR | 스니펫 200개·텍스트 25,000자 이하 | Instructions → 스니펫·텍스트 줄이기 |
| `sources.focused` | WARN | 데이터 소스가 5개 이하 (권장) | Data → 꼭 필요한 테이블만 |
| `sources.description` | WARN | 모든 테이블에 설명이 있다 | Data → 테이블 설명 |
| `columns.entity_matching_scope` | WARN | ID형 컬럼의 값 매칭이 꺼져 있다 | Data → 컬럼 설정 → ID형 컬럼의 값 매칭 끄기 |
| `columns.synonyms` | WARN | 동의어가 있는 컬럼이 하나 이상 | Data → 컬럼 설정 → 동의어 |
| `joins.defined` | WARN | 데이터 소스가 둘 이상이면 조인이 정의돼 있다 | Instructions → Joins |
| `text.length` | WARN | Text Instruction이 2,000자 이하 | Instructions → Text 2,000자 이하로 |
| `text.sections` | WARN | Text에 표준 섹션(PURPOSE·CONSTRAINTS·요약 지침)이 있다 | Instructions → Text 표준 템플릿(PURPOSE·CONSTRAINTS·요약 지침) |
| `sources.allowed` | OFF | 허용된 카탈로그·스키마의 데이터만 쓴다 (기본 꺼짐, 정책에서 켬) | Data → 허용된 카탈로그·스키마의 테이블만 |

## `target` 운영 위치 (노코드)

| 규칙 | 수준 | 보는 것 | 고치는 곳 |
| --- | --- | --- | --- |
| `target.catalogs` | ERROR | 운영 워크스페이스가 데이터 카탈로그를 정해 두었으면 데이터 소스가 모두 그 카탈로그에 있다 (개인 카탈로그의 데이터로 운영하지 않는다. 워크스페이스가 여럿일 때만) | Genie Data → 운영 워크스페이스가 허용한 카탈로그(예: project)의 테이블로 |

## `secrets` 비밀값·금지 파일 (스킬 · 코드 · 노코드)

| 규칙 | 수준 | 보는 것 | 고치는 곳 |
| --- | --- | --- | --- |
| `secrets.values` | ERROR | 파일에 비밀값(Databricks 토큰·GitHub 토큰·AWS 키·개인 키)이 없다 | 비밀값은 Databricks secret·UC 연결로 |
| `secrets.files` | ERROR | 금지 파일(.env, *.pem, id_rsa)이 없다 (.env.example 같은 예시는 됨) | 파일 지우기 |

## 출력

단계마다 `✓`(통과)·`✗`(ERROR 위반) 한 줄, 그 아래 위반·확인할 규칙을 `규칙 ID · 고치는 곳`과 함께 보여 줍니다. API(`POST /api/v1/projects/{id}/validations`)는 단계마다 `rules: [{id, level, passed, message, fix}]`를 돌려줍니다.

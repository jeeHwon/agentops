# SPEC.md 계약 및 운영 확인

`aops`는 Skill·Code·No-code(Genie)를 같은 Registry와 Release 계약으로 관리하며 포털이나 별도 서버는 필요하지 않습니다. Frontmatter에는 현재 `SPEC.md`의 15개 키를 모두 작성하고 옛 키를 함께 쓰지 않습니다. 파일명은 대소문자를 포함해 정확히 `SPEC.md`여야 합니다. 본문 1번 섹션 첫 문장에서 Registry `description`을 추출합니다.

원본 템플릿을 현재 파서로 검사한 결과 frontmatter **15/15 필드가 일치**하고 1~5번 본문 섹션이 존재했습니다. 예시 `model_service` 값은 해당 환경의 모델 가용성을 보장하지 않습니다. 실제 모델 사용 전 접근 권한과 지원 여부를 확인하십시오.

| SPEC.md 키 | 검증 및 저장/실행 경로 |
| --- | --- |
| `agent_id`, `agent_name`, `agent_version` | ID/이름/semver. Release와 Registry의 등록 버전은 `agent_version`과 일치해야 합니다. |
| `build_type`, `serving_type`, `origin` | `skill/code/no-code`. Code는 현재 Agent Bricks Apps (`apps/Apps`), No-code는 `genie`만 지원합니다. Skill은 `apps/Apps/SE/se/Job/job` 선언이며 실제 서빙을 배포하지 않습니다. `origin`은 `databricks/innohub`입니다. |
| `owner`, `owner_group` | 이메일 형식의 소유자와 비어 있지 않은 조직. Registry와 Trace에 기록하며 실제 소유자 권한 검사는 별도입니다. |
| `service_criticality`, `data_sensitivity`, `security_level` | 각각 `상/중/하`, `공개/내부/대외비/개인정보`, `일반/강화`. 로컬 복사형 Skill 릴리스는 `대외비/개인정보` 또는 `강화` 등급의 실행·등록을 거부합니다. |
| `uc_permissions` | `{object, privilege}` 목록. 세 부분으로 된 object와 빈 값 여부 검사; Registry `ARRAY<STRUCT<object:STRING,privilege:STRING>>` 및 Release snapshot에 보존. 실제 UC GRANT 검증·할당은 하지 않습니다. |
| `success_criteria` | 세 비율은 0~1, `latency_p90_seconds`는 유한한 양수. `aops review`는 실제 MLflow Run·Trace와 SPEC의 correctness/relevance/safety 평균·root 지연 p90을 비교합니다. 본문 비수집 Trace는 본문 기반 품질 평가 대상에서 제외합니다. |
| `flagship_model`, `model_service` | Boolean과 비어 있지 않은 모델명. `model_service`는 Codex 프로젝트 `model` 설정에 사용되지만 공급자·인증을 자동으로 연결하지 않습니다. `flagship_model`은 선언일 뿐 모델 정책을 강제하지 않습니다. |

Registry의 `agents`/`agent_versions`는 현재 `SPEC.md` 필드만 저장합니다. `name`/`description`은 검색·표시용 파생 값이며 `current_version`, `model`, checksum, 평가 실행·게이트 필드는 Registry 운영 메타데이터입니다. 같은 설계값을 구형 별칭 컬럼에 중복 보관하지 않습니다. `aops registry-init`는 신규 테이블을 만들고 기존 테이블 컬럼이 현행 스키마와 일치하는지 검사합니다. 기존 테이블이 다르면 실패하며 자동 추가·백필·파괴적 재생성은 하지 않습니다. 불필요한 기존 데이터는 운영자가 백업·의존관계·권한을 확인한 후 **해당 Registry 테이블을 별도로 재생성**해야 합니다. 기존 로컬 Outbox도 현재 스키마 버전이 아니면 시작 시 실패하므로 백업 후 DB 파일을 제거하고 새로 시작합니다. 에이전트 Release 메타데이터는 현재 형식만 읽습니다.

`SPEC.md` 본문은 다섯 번호 섹션이 모두 필요합니다. 평가 데이터가 있으면 로컬 소스의 `eval/eval_dataset.json`에 JSON 목록/객체로 둘 수 있지만, 자동 평가에 연결되거나 Registry Artifact로 배포되지는 않습니다. 정답·PII를 가진 평가 데이터는 별도 접근제어 저장소와 평가 작업에서 관리하십시오. 본문에서 언급된 `databricks.yml` 리소스 선언도 이 Markdown 전용 런타임 번들에는 들어가지 않습니다.

생성 템플릿의 `owner@example.com` 및 `AI-CoE`는 예시 값입니다. 실제 등록 전에 담당자·조직으로 변경하십시오. 현재는 형식 유효성만 검사하며 Registry 등록자의 신원과 `owner`가 일치하는지 검증하지 않습니다.

과거 소문자 파일, 옛 frontmatter 및 이전 형식의 Release는 읽거나 변환하지 않습니다. 현재 템플릿에서 다시 만들고 새 릴리스로 등록합니다. Registry 초기화는 원격 데이터를 자동으로 삭제하지 않습니다.

**배포 전 확인:** 세 유형 모두 `aops configure`에서 사용자가 선택한 Profile·Workspace·Experiment·Warehouse·Registry를 상속합니다. Codex 연동은 `--codex`로 활성화합니다. Codex 프로젝트 설정은 신뢰된 프로젝트에서만 적용될 수 있고 CLI 옵션/사용자 설정으로 덮어쓸 수 있으므로 실제 모델 서비스 사용 여부는 호출 공급자 로그로 검증해야 합니다. Codex는 프로젝트 `.codex/config.toml`의 `[otel]`을 무시하므로 토큰 exporter는 사용자 전역 설정 또는 CLI `-c`에 있어야 합니다. 기존 전역 exporter가 있으면 `aops configure --codex`는 덮어쓰지 않으며 `aops doctor`가 경고합니다. `serving_type` 선언만으로 리소스나 ACL을 생성하지 않으며 Code·Genie의 실제 배포는 `aops deploy --apply`로 별도 실행합니다.

Codex Hook으로 원본에 연결된 MLflow `agent.turn` Trace에는 15개 설계 필드를 태그로 기록합니다. `uc_permissions`와 `success_criteria`는 `|`로 연결된 문자열로 표현됩니다. 소유자 이메일과 UC 객체명도 태그에 포함되므로 MLflow Experiment 읽기 권한을 최소화하십시오. 평가 scorer의 별도 실행이나 외부 도구·모델 호출까지 자동으로 전파되는 것은 아닙니다.

토큰/하위 호출 관측과 민감 Skill 원문 제한은 [MONITORING_STATUS.md](MONITORING_STATUS.md)를 참고하십시오.

Code·Genie의 `aops.yaml`·실행·배포·평가·심사·운영 포인터 의미와 한계는 [CLI_LIFECYCLE.md](CLI_LIFECYCLE.md)를 참고하십시오. 프로젝트에는 framework·소스·리소스와 필요한 예외만 작성하며 공통 환경은 중복 작성하지 않습니다. 공통 schema_version 2 Release는 SPEC snapshot과 공통 설정을 상속한 project snapshot을 보관하며 Skill에서만 Skill snapshot이 필수입니다. `agents.current_version`은 최근 등록, `agent_production.version`은 운영 카탈로그 선택이며 실제 배포와 별개입니다.

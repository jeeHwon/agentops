# console 화면

aops 서버 주소를 브라우저로 열면 나오는 화면입니다. dbx 에디션의 등록·배포 명령을 모두 화면에서 합니다. 화면은 CLI와 같은 `/api/v1`을 부르므로, 어디서 하든 같은 레지스트리에 같은 기록이 남고 같은 규칙(평가 Gate, 승인, 본인 승인 금지)이 적용됩니다.

## 화면과 명령

| 화면 | 하는 일 | CLI |
| --- | --- | --- |
| 에이전트 | 등록된 에이전트 목록, 찾기·거르기, 운영 중·진행 중 요청 수 | `aops list` |
| 에이전트 상세 | 운영·최신 버전, SPEC, 버전(Git 사본 링크), 평가(Gate, 문항별 결과)·배포 요청·배포·감사 이력. 운영 대상 링크: 코드 기반은 운영 앱, 노코드는 운영용·개발용 Genie와 에이전트 폴더 | `aops list`, `aops gates` |
| 에이전트·등록 → 셀프 체크 | 형식 검사(SPEC·스킬·Agent Bricks 프로젝트·노코드 정의 규칙·비밀값)를 바로. 워크스페이스 폴더·등록 버전·올린 폴더, 노코드는 지금 개발용 Genie. 기록하지 않음 | `aops validate` |
| 에이전트 → 평가 | evaluate Job(Gate): 형식 검사를 다시 하고 품질 평가(스킬: 평가셋을 모델이 답하고 판정 모델이 채점, 노코드: Genie 벤치마크·회귀). 단계별·문항별 결과 | `aops evaluate` |
| 에이전트 → 배포 | 버전을 골라 배포 요청 → 평가(Gate) → 승인 → 배포 진행을 실시간으로. 운영보다 낮은 버전은 되돌리기. 노코드는 버전 없이 [배포 요청]: 개발용 정의를 스냅샷으로 떠서 평가하고 배포가 성공할 때 버전을 매김 | `aops deploy`, `aops deploy --version` |
| 에이전트 → 권한 | 대상(group:·user:·sp:)에게 부여·회수: 스킬은 운영 UC Skill 읽기, 코드 기반은 운영 앱 `CAN_USE`, 노코드는 운영용 Genie `CAN RUN` | `aops promote` |
| 에이전트 → 가져오기 | 스킬은 운영 버전 zip(권한 판정은 서버), 코드 기반은 운영 앱, 노코드는 운영용 Genie를 엶 | `aops load` |
| 등록 | 에이전트 폴더(또는 버전 zip)를 올리거나 워크스페이스 폴더 경로를 적어 새 버전 저장. 폴더는 서버가 CLI와 같은 규칙으로 묶어 해시까지 같음(워크스페이스 폴더는 내 토큰으로 읽음, 노트북은 뺌) | `aops register` |
| 새 에이전트 | 종류(스킬·코드·노코드)를 고르고 SPEC 항목(이름·표시 이름·설명·담당 팀·중요도·민감도·보안 등급)을 적음. 스킬·코드 기반은 [내 워크스페이스에 만들기](그 SPEC.md가 든 템플릿을 내 폴더에, 편집기·Genie Code 링크) 또는 [폴더 받기 (zip)], 노코드는 에이전트 폴더·SPEC.md·개발용 Genie를 바로 만듦 | `aops init`, `aops init --type code` |
| 노코드 점검 | 아무 Genie 스페이스 정의를 Gate와 같은 규칙(준수율 15개)으로 바로 점검하고 고치는 곳을 보여 줌. 기록하지 않음 | `aops validate --agent` (노코드) |
| 배포 요청함 | 승인할 요청·내 요청·전체. 승인·반려(사유)·취소 | `aops gates`·`approve`·`reject`·`deploy --cancel` |
| 정책 | 지금 정책과 이력. 워크스페이스 관리자는 평가(Gate)·승인 방식과 승인 그룹을 바꿈(새 정책 버전) | `aops admin setup --only policy` |
| CLI·Genie Code 안내 | 명령 ↔ 화면 ↔ API 대응표 | `GET /api/v1/meta/commands` |

## 스킬·코드 기반을 워크스페이스에서

1. **새 에이전트 → 스킬 기반·코드 기반 → [내 워크스페이스에 만들기]:** `aops init`과 같은 템플릿(SPEC 항목이 든 `SPEC.md`, 이 서버 주소가 든 `aops.toml`)이 내 토큰으로 `/Workspace/Users/<나>/aops/<이름>`(또는 적은 위치)에 올라갑니다. 이미 있는 폴더에는 쓰지 않습니다. 하네스 설정(`.claude`·`.agents`·`CLAUDE.md`·`AGENTS.md`)은 올리지 않습니다. 에이전트가 레지스트리에 바로 잡힙니다(버전은 등록 때).
2. **[편집기에서 열기 (Genie Code)]:** 코드 기반은 `agent/agent.py`, 스킬 기반은 `agent/skills/my-skill/SKILL.md`가 편집기에서 열리고 오른쪽에 Genie Code가 있습니다. 고칠 내용을 말하고, 다 되면 "이 폴더를 aops에 등록해줘"라고 하거나
3. **[이 폴더에서 등록]:** 서버가 내 토큰으로 그 폴더를 읽어 `aops register`와 같은 규칙으로 묶습니다(해시도 같음). 에이전트 화면에도 작업 폴더와 [Genie Code에서 열기]·[이 폴더에서 등록]이 나옵니다.

PC에서 고치려면 [폴더 받기 (zip)]로 같은 템플릿을 받습니다.

## 노코드 (Genie Agent)

1. **새 에이전트 → 노코드:** SPEC 항목과 개발용 Genie를 정합니다. 테이블로 새로 만들기(표준 지침 PURPOSE·CONSTRAINTS·요약 지침이 들어감), 기존 스페이스 복사(원본 그대로), 기존 스페이스 옮기기(ID·대화 이력 유지). 공개 그룹(`consumer_group`)은 배포 때 운영용 Genie에 `CAN RUN`을 받습니다.
   - aops 서비스 주체가 에이전트 폴더 `/Workspace/aops/no-code/<이름>`을 만들고 나와 담당 팀에 `CAN MANAGE`를 줍니다. `SPEC.md`가 그 폴더에 생기고, 여기서 고치면 다음 배포 요청에 들어갑니다.
   - 개발용 Genie는 **내 토큰**으로 그 폴더에 만듭니다(앱의 `genie` 사용자 권한 범위). 내 테이블·warehouse 권한이 그대로 적용되고, 서비스 주체는 폴더에서 권한을 받아 정의를 읽습니다.
2. **[Genie에서 열기]:** 개발용 Genie에서 예제 SQL·벤치마크·지침·컬럼 설정을 채웁니다.
3. **[셀프 체크]:** Gate와 같은 정의 규칙(15개)의 준수율과 고치는 곳. 기록하지 않습니다. **[평가]**는 배포 요청 없이 Gate만 돌립니다(아래 4의 평가).
4. **[배포 요청]:** 개발용 정의와 `SPEC.md`를 스냅샷으로 떠서 평가(`format`·`definition`·`secrets`, Genie 벤치마크 `evaluate`: 정확도·운영 버전 대비 회귀, `integrity`, `record`) → 정책대로 승인 → 배포 Job이 운영용 Genie(서비스 주체 소유, `/Workspace/aops/no-code/_production/<이름>`)를 만들거나 고침 → 이때 버전 등록(처음 1.0.0, 데이터 소스가 바뀌면 major, 그 밖 minor). 검사가 끝날 때까지 개발용을 고치면 `stale`로 반려됩니다.
5. **[권한]·[가져오기]:** 운영용 Genie `CAN RUN` 부여·회수, 운영용 Genie 열기. 받은 사람도 테이블 `SELECT`와 warehouse 사용 권한이 있어야 답을 받습니다.

운영용 Genie는 서비스 주체가 만들고 벤치마크도 서비스 주체로 돌므로, 쓰는 테이블에 서비스 주체의 `SELECT`가 필요합니다.

화면에 없는 것: 하네스 실행(`aops run`, `aops session`)은 PC나 Genie Code에서 합니다. 레지스트리·서버·Job·권한 설치(`aops admin setup`의 정책 외 단계)는 관리자 CLI입니다.

## 쓰는 법

- **Databricks App(aops 서버):** `aops admin setup`이 배포한 앱 주소를 엽니다. Databricks 로그인을 그대로 쓰고, 앱의 `CAN USE` 권한이 있으면 됩니다. 앱의 기본 사용자 권한 범위(`iam.current-user:read`·`iam.access-control:read`, 모든 앱에 늘 들어 있음)로 내 그룹을 읽어 담당 팀·승인 그룹·관리자를 판정합니다. `aops admin setup`이 더하는 범위: `files`(스킬 가져오기), `genie`(노코드 개발용 Genie를 내 권한으로), `workspace.workspace`(내 폴더에 템플릿). 범위가 바뀐 뒤에는 새로 로그인할 때 동의 화면이 나옵니다. 바뀌기 전에 연 화면(앱 세션)은 예전 범위의 토큰을 계속 쓰므로, 시크릿 창으로 열거나 앱 주소의 쿠키를 지운 뒤 다시 엽니다.
- **내 PC에서 시험:** 레지스트리에 연결한 `aops serve`를 띄우고 `http://127.0.0.1:8000`을 엽니다. 이때 화면의 사용자는 그 PC의 Databricks CLI 프로필 사용자입니다.

  ```bash
  AOPS_PROFILE=<프로필> AOPS_REGISTRY=<catalog>.<schema> AOPS_WAREHOUSE_ID=<warehouse> \
  AOPS_DIST_WHEEL=/Volumes/<catalog>/<schema>/aops_dist/aops_cli-<버전>-py3-none-any.whl aops serve
  ```

  `AOPS_DIST_WHEEL`은 운영 서버와 같은 경로로 둡니다(다르면 서버가 Job 설정을 그 패키지로 바꿈). PC에서 띄운 서버로 처음 등록하면 볼륨 주인이 그 사람이 되어 서비스 주체의 평가 Job이 읽지 못하므로, 시험용 등록은 운영 서버로 합니다.
- 화면은 빌드 없이 서버가 바로 내주는 파일 세 개(`src/aops/server/web/`)입니다. 외부 글꼴·라이브러리를 쓰지 않아 닫힌 망에서도 됩니다.

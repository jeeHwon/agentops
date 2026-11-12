# console 화면

aops 서버 주소를 브라우저로 열면 나오는 화면입니다. dbx 에디션의 등록·배포 명령을 모두 화면에서 합니다. 화면은 CLI와 같은 `/api/v1`을 부르므로, 어디서 하든 같은 레지스트리에 같은 기록이 남고 같은 규칙(검사, 승인, 본인 승인 금지)이 적용됩니다.

## 화면과 명령

| 화면 | 하는 일 | CLI |
| --- | --- | --- |
| 에이전트 | 등록된 에이전트 목록, 찾기·거르기, 운영 중·진행 중 요청 수 | `aops list` |
| 에이전트 상세 | 운영·최신 버전, SPEC, 버전(Git 사본 링크), 검사·배포 요청·배포·감사 이력 | `aops list`, `aops gates` |
| 에이전트 → 검사 | 검사 Job을 돌리고 검사별 결과를 보여 줌 | `aops validate` |
| 에이전트 → 배포 | 버전을 골라 배포 요청 → 검사 → 승인 → 배포 Job 진행을 실시간으로. 운영보다 낮은 버전은 되돌리기 | `aops deploy`, `aops deploy --version` |
| 에이전트 → 권한 | 대상(group:·user:·sp:)에게 운영 UC Skill 읽기 부여·회수 | `aops promote` |
| 에이전트 → 가져오기 | 운영 버전 zip 받기 (권한 판정은 서버) | `aops load` |
| 등록 | 에이전트 폴더(또는 버전 zip)를 올려 새 버전 저장. 폴더는 서버가 CLI와 같은 규칙으로 묶어 해시까지 같음 | `aops register` |
| 새 에이전트 | 스킬 기반·코드 기반 에이전트 폴더(zip)를 받음. aops.toml에 이 서버 주소가 들어 있음 | `aops init`, `aops init --type code` |
| 노코드 점검 | Genie 스페이스 정의를 배포 Gate와 같은 규칙(준수율 15개)으로 바로 점검하고 고치는 곳을 보여 줌. 기록하지 않음 | `aops validate` (노코드) |
| 배포 요청함 | 승인할 요청·내 요청·전체. 승인·반려(사유)·취소 | `aops gates`·`approve`·`reject`·`deploy --cancel` |
| 정책 | 지금 정책과 이력. 워크스페이스 관리자는 검사·승인 방식과 승인 그룹을 바꿈(새 정책 버전) | `aops admin setup --only policy` |
| CLI·Genie Code 안내 | 명령 ↔ 화면 ↔ API 대응표 | `GET /api/v1/meta/commands` |

화면에 없는 것: 하네스 실행(`aops run`, `aops session`)은 PC나 Genie Code에서 합니다. 레지스트리·서버·Job·권한 설치(`aops admin setup`의 정책 외 단계)는 관리자 CLI입니다.

## 쓰는 법

- **Databricks App(aops 서버):** `aops admin setup`이 배포한 앱 주소를 엽니다. Databricks 로그인을 그대로 쓰고, 앱의 `CAN USE` 권한이 있으면 됩니다. 앱이 사용자 권한 범위 `iam.current-user:read`·`iam.access-control:read`로 내 그룹을 읽어 담당 팀·승인 그룹·관리자를 판정합니다(`admin setup --only app`이 더함).
- **내 PC에서 시험:** 레지스트리에 연결한 `aops serve`를 띄우고 `http://127.0.0.1:8000`을 엽니다. 이때 화면의 사용자는 그 PC의 Databricks CLI 프로필 사용자입니다.

  ```bash
  AOPS_PROFILE=<프로필> AOPS_REGISTRY=<catalog>.<schema> AOPS_WAREHOUSE_ID=<warehouse> \
  AOPS_DIST_WHEEL=/Volumes/<catalog>/<schema>/aops_dist/aops_cli-<버전>-py3-none-any.whl aops serve
  ```

  `AOPS_DIST_WHEEL`은 운영 서버와 같은 경로로 둡니다(다르면 서버가 Job 설정을 그 패키지로 바꿈). PC에서 띄운 서버로 처음 등록하면 볼륨 주인이 그 사람이 되어 서비스 주체의 검사 Job이 읽지 못하므로, 시험용 등록은 운영 서버로 합니다.
- 화면은 빌드 없이 서버가 바로 내주는 파일 세 개(`src/aops/server/web/`)입니다. 외부 글꼴·라이브러리를 쓰지 않아 닫힌 망에서도 됩니다.

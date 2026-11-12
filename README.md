# aops

Databricks 위에서 AI 에이전트를 개발부터 운영까지 관리하는 오픈소스 도구입니다. Claude Code·Codex 같은 하네스로 에이전트를 만들고, Databricks 기능으로 기록·검증·배포·운영합니다.

![aops 아키텍처](docs/architecture.png)

## 주요 기능

목표로 하는 기능입니다. 지금 동작하는 범위는 [지금 되는 것](#지금-되는-것-스킬-기반)을 보세요.

- **에이전트 3종을 하나로:** 스킬 기반(누구나), 코드 기반(AI 개발자), 노코드(데이터 실무자, Genie Agent)를 같은 레지스트리·검사·배포·운영으로 관리합니다. 코드·노코드는 dbx 에디션부터 씁니다.
- **세션 이어가기:** 하네스를 바꿔도 대화와 메모리가 이어집니다.
- **구성별 비교:** 같은 질문을 하네스·모델·가드레일 구성별로 돌려 나란히 비교합니다.
- **평가·가드레일 시험:** MLflow로 채점하고, 레드팀 사례로 가드레일을 시험합니다.
- **검증 후 배포:** 등록·배포할 때만 관리자 정책을 검사하고, 승인된 버전만 배포합니다.
- **찾기·가져오기:** 등록된 에이전트를 목록에서 찾고, 권한을 받은 사람은 가져와 씁니다.
- **운영 모니터링:** 사용량·비용과 실행 중인 에이전트를 한 화면에서 봅니다.
- **console 화면:** aops 서버 주소를 브라우저로 열면 등록·검사·배포·승인·권한·정책을 화면에서 합니다 ([console](docs/console.md)).
- **Custom Front:** 조직의 웹·서비스가 aops 서버 API에 연결해 dbx 기능을 그대로 씁니다 (custom 에디션).

## 지금 되는 것 (스킬 기반)

지금은 **스킬 기반 에이전트**의 만들기부터 가져다 쓰기까지 dbx 에디션에서 동작합니다. **코드 기반(Agent Bricks)** 은 만들기(`aops init --type code`)·등록·검사·배포(aops 서버가 서비스 주체로 `agentbricks deploy`)·권한(앱 `CAN_USE`)·가져오기(앱 링크)가 들어갔고 운영 실측은 다음 차례입니다. **노코드(Genie Agent)** 는 console에서 만들기(에이전트 폴더·SPEC.md·개발용 Genie)·점검·배포 요청(스냅샷 검사 → 승인 → 운영용 Genie, 이때 버전 등록)·권한(`CAN RUN`)·가져오기(운영용 Genie 링크)가 들어갔고, 벤치마크 평가와 운영 실측은 다음 차례입니다.

| 단계 | 명령 | 도는 곳 | 쓰는 Databricks 기능 |
| --- | --- | --- | --- |
| 만들기 | `aops init my-agent --edition dbx` | 내 PC | 없음 (스킬 템플릿, `SPEC.md`, `aops.toml`) |
| 실행 | `aops run "<질문>"` | 내 PC (Claude Code·Codex) | MLflow trace (도구·스킬, 토큰·비용) |
| 하네스 바꾸기 | `aops session switch --harness codex` | 내 PC | 없음 (앞 대화를 넘겨 이어 감) |
| 등록 | `aops register` | aops 서버 | UC Volume(버전 zip), 레지스트리 Delta 테이블 |
| 검사·승인·배포 | `aops deploy` (검사만: `aops validate`, 취소: `aops deploy --cancel`) | aops 서버 | Lakeflow Jobs (서버리스, 서비스 주체), UC Skill (첫 배포 때 생성) |
| 승인 (승인자) | `aops gates` · `aops approve <gate>` · `aops reject <gate> --reason <이유>` | aops 서버 | 레지스트리 (`approvals`, 감사 이력) |
| 목록 | `aops list` | aops 서버 | 레지스트리 |
| 권한 | `aops promote my-agent --to group:<계정 그룹>` | aops 서버 | UC Skill 권한 (`READ_VOLUME`) |
| 가져오기 | `aops load my-agent` | aops 서버 → 내 PC | 요청한 사람의 권한 확인, 해시 확인 |
| 되돌리기 | `aops deploy --version <이전 버전>` | aops 서버 | 같음 |
| 준비 (관리자) | `aops admin setup --profile <프로필> --registry <catalog>.<schema> --warehouse <ID>` | 관리자 PC | Databricks App(aops 서버), Jobs, 레지스트리 |

- `aops run`은 내 PC에 설치된 `claude`·`codex` CLI를 SDK로 띄웁니다. 모델 호출은 하네스 설정을 따릅니다. Claude는 기본으로 프로젝트 설정만 읽습니다(개인 MCP·hook은 `[agent] user_settings = true`로 켬).
- 등록부터는 aops 서버가 서비스 주체로 레지스트리·Job·UC Skill을 씁니다. 사람은 요청만 합니다.
- 배포 요청은 검사(Gate)를 통과해야 승인으로 넘어갑니다. `init` 템플릿을 고치지 않은 채(`my-skill`, 템플릿 설명) 올리면 반려됩니다.
- 승인 방식은 관리자 정책을 따릅니다: `auto`(자동), `optional`(SPEC 중요도 `low`·민감도 `public`·`internal`이면 자동), `required`(늘 승인자). 승인자는 console 화면의 배포 요청함이나 `aops gates`·`aops approve`로 결재하고, 요청자 본인은 승인할 수 없습니다.
- aops 서버 API 중 지금 처리되는 경로는 [API](docs/api.md)의 "지금 상태"에 있습니다. 나머지는 형식만 있고 `501`을 돌려줍니다.

### 한 바퀴 돌려 보기

**설치** (Python 3.11 이상, [uv](https://docs.astral.sh/uv/)). 하네스 `claude`(Claude Code)·`codex`(Codex)는 따로 설치해 로그인해 둡니다.

```bash
uv tool install "aops-cli[claude,codex,dbx] @ git+https://github.com/jeeHwon/agentops.git"
```

**관리자 (조직에 한 번)**: 저장소를 받은 폴더에서 실행합니다. 처음에는 미리보기만 하고, `--apply`로 실행합니다.

```bash
git clone https://github.com/jeeHwon/agentops.git && cd agentops
uv run --extra dbx aops admin setup --profile <프로필> --registry <catalog>.<schema> --warehouse <SQL warehouse ID> \
  --approval-mode auto          # 승인자를 둘 때: --approval-mode required|optional --approver-group <계정 그룹>
uv run --extra dbx aops admin setup ... --apply
```

aops 서버 주소(`https://<app>.databricksapps.com`)를 사용자에게 알려 줍니다.

**사용자**

```bash
export AOPS_PROFILE=<프로필> AOPS_SERVER=https://<aops 서버 주소>
aops init my-agent --edition dbx && cd my-agent
# SPEC.md의 description, agent/instructions.md, agent/skills/my-skill/ (폴더 이름·SKILL.md)을 내 것으로 고친다
aops run "<질문>"
aops session switch --harness codex && aops run "<이어지는 질문>"
aops session show                    # 구간별 하네스, 인계, 쓴 스킬, MLflow trace
aops register                        # 1.0.0. 다음부터 --bump fix|minor|major
aops deploy                          # 검사 → 승인 → 운영 반영 (약 2분). 승인자가 필요하면 gate ID를 보여 주고 끝남
aops list
aops promote my-agent --to group:<계정 그룹>
```

**승인자** (정책이 `required`·`optional`일 때)

```bash
aops gates                           # 내가 승인할 차례인 요청
aops approve <gate_id>               # 또는 aops reject <gate_id> --reason <이유>
```

**받는 사람** (`promote`로 권한을 받은 뒤)

```bash
aops load my-agent && cd my-agent && aops run "<질문>"
```

## 전체 명령어 (설계)

아래는 설계한 전체 명령입니다. `dev`, `run <질문 파일>`, `evaluate`, `monitor`는 아직 없습니다. 명령마다 지금 동작하는 범위는 [명령어](docs/commands.md)에 표시했습니다.

```bash
aops init my-agent         # 새 에이전트
aops dev                   # 개발 화면
aops run questions.yaml    # 구성별 비교 실행
aops evaluate              # 평가
aops register              # 버전 저장
aops validate              # 관리자 정책 자가 점검
aops deploy                # 배포 요청 → 승인 → 운영 반영
aops promote --to <대상>   # 대상자 권한 부여
aops list · aops load      # 찾기·가져오기
aops monitor               # 운영 현황
aops serve                 # aops API 서버 (custom)
```

## 문서

- [개요](docs/overview.md): 에이전트 3종, 단계별 Databricks 기능, 원칙, 에디션
- [명령어](docs/commands.md): 전체 명령과 옵션, 지금 동작하는 범위
- [console 화면](docs/console.md): 화면별로 하는 일과 대응하는 명령
- [프로젝트 폴더](docs/project.md): 폴더 구조와 `aops.toml`
- [레지스트리·승인](docs/registry.md): 관리자 정책, aops 서버, 권한, 테이블
- [API](docs/api.md): `aops serve`의 HTTP API와 명령 대응표

## 라이선스

[MIT](LICENSE)

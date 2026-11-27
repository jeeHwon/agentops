# 여러 워크스페이스 (admin · 개발 · 운영)

[README](../README.md) · [개요](overview.md) · [명령어](commands.md) · [프로젝트 폴더](project.md) · [레지스트리·승인](registry.md) · [API](api.md) · [console](console.md) · [앱 밖에 띄우기](container.md) · [여러 워크스페이스](workspaces.md)

aops는 기본으로 워크스페이스 하나에서 돕니다. 서버, 레지스트리, 개발, 운영이 모두 그 워크스페이스에 있습니다. 조직에 따라 서버는 관리용(admin·MLOps) 워크스페이스에 두고, 사람은 개인·프로젝트 워크스페이스에서 개발하고, 운영 자원은 프로젝트 워크스페이스에 둡니다. 정책에 워크스페이스를 적으면 aops가 이렇게 나뉜 구조에서 돕니다.

> **지금:** 코드와 단위 테스트까지 됩니다. 같은 메타스토어의 실제 워크스페이스 여러 개에서는 아직 시험하지 않았습니다. 워크스페이스가 하나일 때의 동작은 그대로입니다.

## 역할

| 역할 | 하는 일 | 개수 |
| --- | --- | --- |
| 서버 워크스페이스 (home) | aops 서버, 레지스트리(SQL warehouse), 평가·배포 Job, 검토·채점 모델 | 하나 |
| 개발 (`dev`) | 사람이 만드는 곳: 에이전트 폴더, 개발용 Genie, Genie Code, 개발 실험 | 여럿 |
| 운영 (`target`) | 운영용 Genie, 코드 기반 운영 앱 | 하나 이상 (기본 하나) |

- 한 워크스페이스가 두 역할을 가질 수 있습니다(예: 프로젝트 워크스페이스 = 개발 + 운영).
- 목록에 개발 워크스페이스가 없으면 서버 워크스페이스가 개발을 맡고, 운영 워크스페이스가 없으면 서버 워크스페이스가 운영을 맡습니다.
- 스킬 기반의 운영은 UC Skill(메타스토어)이라 어느 워크스페이스에도 매이지 않습니다.

## 원칙

1. **사람 권한이 필요한 일은 그 사람의 토큰이 통하는 워크스페이스에서만 합니다.** 앱이 넘겨주는 사용자 토큰은 앱이 도는 워크스페이스에서만 쓸 수 있습니다(Databricks Apps 문서). aops는 토큰을 다른 워크스페이스로 보내지 않습니다.
2. **워크스페이스를 넘나드는 일은 aops 서비스 주체 하나가 합니다.** 계정 수준 서비스 주체를 서버·개발·운영 워크스페이스에 모두 배정합니다. 관리자일 필요는 없습니다(필요한 권한은 아래).
3. **운영 자원은 운영 워크스페이스에만 있고, 쓰기는 서비스 주체만 합니다.** 개발자는 운영 워크스페이스에 들어가지 않아도 aops(배포 상태·리포트·운영 버전)로 운영을 봅니다.

## 설정

정책에 둡니다. 버전이 남고 평가·배포 Job도 같은 값을 읽습니다.

```bash
aops admin setup --profile <서버 워크스페이스 관리자> --registry <catalog>.<schema> --warehouse <ID> \
  --workspace "personal=https://adb-1.azuredatabricks.net;roles=dev" \
  --workspace "project=https://adb-2.azuredatabricks.net;roles=dev,target;catalogs=project;warehouse=<프로젝트 warehouse ID>" \
  --default-target project --workspace-secret-scope aops --only tables,policy,grants --apply
```

| 옵션 | 뜻 |
| --- | --- |
| `--workspace NAME=URL;roles=…` | 워크스페이스를 더하거나 바꿉니다. 여러 번. 주지 않으면 지금 정책 값을 그대로 둡니다 |
| `catalogs=a,b` | 운영 데이터가 있어도 되는 카탈로그. 노코드 데이터 소스가 밖에 있으면 셀프 체크·평가 ERROR(개인 카탈로그로 운영하지 않음) |
| `warehouse=ID` | 그 워크스페이스의 SQL warehouse. 운영용 Genie와 그 워크스페이스에서 만드는 개발용 Genie의 기본 |
| `--default-target NAME` | SPEC.md `target_workspace`가 없을 때의 운영 워크스페이스 |
| `--workspace-secret-scope SCOPE` | 다른 워크스페이스를 부를 서비스 주체의 OAuth client ID·secret이 든 시크릿 범위(키 `client-id`·`client-secret`). `grants`가 서버 서비스 주체에 READ를 줍니다 |
| `--no-workspaces` | 목록을 지웁니다(다시 워크스페이스 하나) |

`tables` 단계가 레지스트리에 위치 열(`genie_agents.dev_workspace`·`prod_workspace`, `agent_production.workspace`)을 더합니다. 워크스페이스를 적은 뒤 이 열이 없으면 서버가 만들기·배포 요청을 막고 `--only tables`를 안내합니다.

### 다른 워크스페이스 준비

정책에 적은 워크스페이스마다, 그 워크스페이스의 관리자 프로필로 한 번 실행합니다.

```bash
aops admin setup --profile <그 워크스페이스 관리자> --registry <catalog>.<schema> --warehouse <그 워크스페이스 warehouse ID> \
  --for-workspace personal --service-principal <aops 서비스 주체 application ID> [--server-url https://<앱 밖 서버>] --apply
```

1. aops 서비스 주체가 그 워크스페이스에 배정돼 있는지 봅니다. 없으면 계정 관리자에게 배정을 부탁하라고 하고 멈춥니다.
2. `/Workspace/aops/no-code`를 만들고 서비스 주체에 `CAN_MANAGE`(개발 폴더, 운영용 Genie 폴더 `_production`), warehouse `CAN_USE`.
3. `--server-url`이 있으면 Genie Code 스킬 `aops`를 그 워크스페이스에 올립니다(아래 "들어오는 길").

노코드가 쓰는 테이블은 데이터 주인이 서비스 주체에 `SELECT`를 줍니다(운영용 Genie와 벤치마크가 서비스 주체 권한으로 돕니다).

## 서비스 주체의 자격

서버가 다른 워크스페이스를 부를 때 이 순서로 찾습니다.

1. `AOPS_WORKSPACE_PROFILES=name=profile,…`: PC에서 띄운 서버가 워크스페이스마다 CLI 프로필을 씁니다(시험용)
2. 정책의 시크릿 범위(`--workspace-secret-scope`): 서버와 Job이 읽습니다
3. 서버 자신의 OAuth 클라이언트(`DATABRICKS_CLIENT_ID`·`SECRET`, 앱 밖 서버의 서비스 주체)

Databricks App의 서비스 주체는 앱 하나에 묶이고 앱을 지우면 함께 지워지므로, 다른 워크스페이스에는 전용 서비스 주체를 씁니다. 평가·배포 Job은 서버 워크스페이스에서 돌고 다른 워크스페이스는 2번으로 부르므로, 노코드를 다른 워크스페이스에서 만들거나 운영하면 시크릿 범위가 필요합니다.

## 들어오는 길: 요청이 어느 워크스페이스에서 왔나

CLI와 Genie Code는 앱 밖 서버를 부를 때 자기 토큰과 함께 `X-Aops-Workspace: <그 워크스페이스 주소>`를 보냅니다. 서버는 **정책에 있는 주소일 때만** 받고, 그 워크스페이스의 SCIM `/Me`로 신원을 확인합니다. 모르는 주소면 토큰을 보내지 않고 400 `unknown_workspace`입니다. Databricks App 안에서는 토큰이 늘 앱의 워크스페이스 것이라 이 헤더를 읽지 않습니다.

| 서버 위치 | 개발 워크스페이스에서 되는 것 |
| --- | --- |
| 앱 밖 (컨테이너, [앱 밖에 띄우기](container.md)) | 전부. Genie Code·CLI가 그 워크스페이스 토큰으로 서버를 부르고, 내 폴더 만들기·개발용 Genie·내 테이블 목록도 그 워크스페이스에서 내 권한으로 |
| 서버 워크스페이스의 앱 | 브라우저 화면에서 등록(폴더·zip 올리기)·배포 요청·승인·권한. 계정 사용자는 앱 워크스페이스에 배정되지 않아도 앱을 씁니다. 다른 워크스페이스의 노트북은 앱용 토큰을 받을 수 없어(노트북 토큰 교환은 같은 워크스페이스에서만) Genie Code "등록해줘"와 그 워크스페이스에서의 내 권한 일은 안 됩니다 |

사람 권한 일은 요청이 온 워크스페이스에서 합니다.

- 만들기(내 폴더, 노코드 개발용 Genie)는 개발 워크스페이스에서만. 아니면 409 `not_dev_workspace`.
- 에이전트를 만들거나 등록한 워크스페이스 폴더는 그 워크스페이스의 토큰으로만 읽습니다. 다른 곳에서 오면 409 `wrong_workspace`(그 워크스페이스의 Genie Code·CLI에서).
- 만들기 화면의 warehouse 기본값은 그 워크스페이스의 `warehouse`입니다.

## 운영으로 넘기기

- 배포 요청 때 운영 워크스페이스가 정해져 요청 기록에 남습니다(SPEC.md `target_workspace`, 없으면 `--default-target`). 리포트와 승인 화면에 보입니다.
- **노코드:** 평가(벤치마크)는 개발 워크스페이스의 개발용 Genie에서 돕니다. 승인되면 배포 Job이 운영 워크스페이스의 `/Workspace/aops/no-code/_production/<이름>`에 운영용 Genie를 만들거나 고칩니다(warehouse는 그 워크스페이스의 것). 운영 워크스페이스를 바꾸면 새 곳에 새로 만들고, 옛 곳의 운영용 Genie는 그대로 둡니다.
- **코드 기반:** aops 서버가 운영 워크스페이스에 그곳의 서비스 주체로 `agentbricks deploy`합니다.
- **권한(promote)·가져오기(load):** 운영 자원이 있는 워크스페이스에서 합니다. 노코드 CAN RUN은 그 워크스페이스에 배정된 사람·그룹에게만 줄 수 있습니다(아니면 결과에 실패로 남음). 운영 앱은 계정 사용자면 배정 없이 씁니다.

## 개발자가 운영 워크스페이스를 봐야 하나

보지 않는 것을 권합니다. 개발자가 운영에서 알아야 할 것(배포 상태·리포트·승인·운영 버전)은 aops가 보여 주고, 운영 쓰기는 서비스 주체만 합니다. 운영 버전을 직접 써 봐야 하면 소비자로서 권한(promote)을 받습니다. 운영 사용 기록(트레이스)을 개발 워크스페이스에서 읽는 것은 UC에 저장한 트레이스를 읽기 전용 바인딩으로 여는 방식이 설계입니다.

## 카탈로그 바인딩

- 레지스트리 카탈로그는 서버 워크스페이스에만 묶어도 됩니다(읽고 쓰는 것은 서버와 Job). 사람의 스킬 가져오기도 서버를 거칩니다.
- 노코드 운영 데이터 카탈로그(`catalogs`)는 운영 워크스페이스에 묶여 있어야 운영용 Genie가 읽습니다.

## 서비스 주체에 필요한 권한

관리자 권한은 필요 없습니다.

| 어디 | 권한 |
| --- | --- |
| 서버 워크스페이스 | 레지스트리 스키마 UC 권한, warehouse `CAN_USE`, 시크릿 범위 READ (`grants`) |
| 개발 워크스페이스 | 배정, `/Workspace/aops/no-code` `CAN_MANAGE`, warehouse `CAN_USE` (`--for-workspace`) |
| 운영 워크스페이스 | 배정, `/Workspace/aops/no-code` `CAN_MANAGE`, warehouse `CAN_USE`, 앱 만들기(코드 기반, 워크스페이스 사용자 기본 권한) |
| 데이터 | 노코드가 쓰는 테이블 `SELECT` (데이터 주인) |

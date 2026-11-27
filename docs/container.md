# aops 서버를 앱 밖에 띄우기 (컨테이너 · 내 PC)

aops 서버는 보통 Databricks App(`aops admin setup`의 `app` 단계)으로 돕니다. 같은 서버를 조직의 컨테이너 플랫폼이나 PC에 띄울 수도 있습니다. 코드는 같고, Databricks Apps가 해 주던 세 가지를 다른 방법으로 채웁니다.

| Apps가 해 주던 것 | 앱 밖에서 |
| --- | --- |
| 서버의 신원 (앱 서비스 주체) | 전용 서비스 주체의 OAuth M2M (환경 변수) |
| 사람 로그인과 사용자 토큰 | 서버의 Databricks OAuth 로그인 (`/auth/login`, 계정의 App connection) |
| HTTPS 주소, 네트워크 | 조직의 로드 밸런서·인그레스 |

## 신원은 이렇게 나뉩니다

- **서버:** 레지스트리 기록, 평가·배포 Job, Git 사본, 운영용 Genie·코드 운영 앱을 서비스 주체 권한으로 합니다. `DATABRICKS_HOST`·`DATABRICKS_CLIENT_ID`·`DATABRICKS_CLIENT_SECRET`을 주면 Databricks SDK가 OAuth M2M으로 씁니다.
- **사람:** 내 테이블 목록, 내 폴더에 템플릿 쓰기, 개발용 Genie 만들기처럼 사람 권한이 필요한 일은 그 사람의 토큰으로 합니다. 누구인지와 역할은 앱에서와 같이 그 토큰의 SCIM `/Me`(이메일·그룹)로 정합니다. 관리자는 `admins` 그룹, 승인자는 정책의 승인 그룹·지정 승인자, 담당자는 SPEC.md입니다. 역할을 따로 저장하지 않으므로 그룹을 바꾸면 5분 안에 반영됩니다.
- **들어오는 길:** 브라우저(console)는 로그인 쿠키, CLI·다른 서비스는 `Authorization: Bearer <Databricks OAuth 토큰>`입니다. 노트북·Genie Code는 서버 주소가 앱(`*.databricksapps.com`)이 아니면 노트북 자신의 토큰을 보냅니다.
- **다른 워크스페이스에서 온 요청:** 정책에 워크스페이스가 여럿이면 CLI·Genie Code가 토큰과 함께 `X-Aops-Workspace: <그 워크스페이스 주소>`를 보냅니다. 서버는 정책에 있는 주소만 받고 그 워크스페이스에서 토큰을 확인합니다. 앱 밖 서버 하나로 여러 개발 워크스페이스의 Genie Code까지 받을 수 있습니다([여러 워크스페이스](workspaces.md)).

## 브라우저 로그인 (Databricks OAuth)

1. **계정 콘솔 → Settings → App connections → Add connection** (계정 관리자):
   - Redirect URL: `https://<서버 주소>/auth/callback` (PC에서 시험하면 `http://localhost:8000/auth/callback`)
   - Access scopes: **All APIs** (`all-apis`). `offline_access`(refresh token)는 자동으로 붙습니다. 범위를 잘게 나누려면 계정 API(`custom-app-integrations`)로 만들어야 합니다.
   - Client secret: 선택입니다. 만들면 `AOPS_OAUTH_CLIENT_SECRET`으로 주고(confidential), 없으면 PKCE만으로 됩니다.
   - 토큰 수명: 기본 access token 1시간, refresh token 7일. single-use refresh token은 켜지 않는 것을 권합니다(복제본 여럿이 같은 토큰을 동시에 갱신하면 한쪽이 로그아웃됩니다).
2. **흐름:** `/auth/login` → 워크스페이스 `/oidc/v1/authorize`(PKCE S256, state) → 회사 SSO(워크스페이스에 연동된 IdP) → `/auth/callback`이 코드를 `/oidc/v1/token`에서 토큰으로 바꾸고 SCIM `/Me`로 계정을 확인 → 세션 쿠키 → 원래 화면.
3. **세션:** refresh token은 서버 키로 암호화한 HttpOnly·SameSite=Lax 쿠키(`aops_session`, 주소가 https이면 Secure)에만 있습니다. access token은 서버 메모리에 두고, 없거나 만료되면 쿠키의 refresh token으로 받아 옵니다(재시작·다른 복제본도 그대로). 데이터베이스는 쓰지 않습니다. `AOPS_SESSION_KEY`를 바꾸면 모두 로그아웃됩니다.
4. **보호:** 쿠키로 들어온 요청이 무언가를 바꾸면(POST·PUT·PATCH·DELETE) `Origin`이 `AOPS_PUBLIC_URL`과 같아야 합니다. 로그인 뒤 돌아갈 곳은 이 서버 안의 경로만 받습니다. Bearer 요청은 쿠키를 보지 않습니다.
5. **console:** 로그인이 필요하면 로그인 화면으로 가고, 사이드바 아래 [로그아웃]이 생깁니다. 로그아웃은 aops 세션만 지웁니다. 브라우저의 Databricks 로그인은 그대로라 [다시 로그인]을 누르면 바로 들어옵니다.

## 환경 변수

| 이름 | 값 | 비밀 |
| --- | --- | --- |
| `DATABRICKS_HOST` | `https://<워크스페이스>` | |
| `DATABRICKS_CLIENT_ID` | 서버 서비스 주체의 application ID | |
| `DATABRICKS_CLIENT_SECRET` | 그 서비스 주체의 OAuth secret | 예 |
| `AOPS_REGISTRY` | `<catalog>.<schema>` | |
| `AOPS_WAREHOUSE_ID` | SQL warehouse ID | |
| `AOPS_DIST_WHEEL` | `/Volumes/<catalog>/<schema>/aops_dist/aops_cli-<버전>-py3-none-any.whl` (Job이 설치) | |
| `AOPS_OAUTH_CLIENT_ID` | App connection의 client ID (주면 로그인이 켜짐) | |
| `AOPS_OAUTH_CLIENT_SECRET` | App connection의 secret (confidential일 때) | 예 |
| `AOPS_PUBLIC_URL` | 사람이 여는 주소 `https://…` (리다이렉트 주소와 허용 출처) | |
| `AOPS_SESSION_KEY` | 쿠키 키: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | 예 |
| `AOPS_OAUTH_SCOPES` | 기본 `all-apis offline_access` | |
| `AOPS_SESSION_DAYS` | 세션 쿠키 수명, 기본 7 (refresh token 수명과 맞춤) | |
| `AOPS_OAUTH_HOST` | `/oidc/v1/authorize`·`/oidc/v1/token`이 워크스페이스가 아닌 곳에 있을 때 그 주소. 계정 확인(SCIM)은 늘 워크스페이스 | |

비밀값은 플랫폼의 시크릿(쿠버네티스 Secret 등)으로 넣고 이미지에는 넣지 않습니다.

## 준비 순서

1. **서비스 주체:** 계정이나 워크스페이스에서 서버용 서비스 주체를 만들고 OAuth secret을 발급합니다.
2. **레지스트리·권한:** 관리자 프로필로 `aops admin setup`을 돌리고 `--service-principal`에 그 서비스 주체를 줍니다. `app` 단계는 건너뛰고, 권한(레지스트리 USE·SELECT·MODIFY, 패키지 볼륨, Git 연결, 노코드 폴더 CAN_MANAGE, warehouse CAN_USE)을 그 서비스 주체에 줍니다. 마지막에 컨테이너에 넣을 환경 변수를 보여 줍니다.

   ```bash
   aops admin setup --profile <관리자 프로필> --registry <catalog>.<schema> --warehouse <ID> \
     --service-principal <application ID> --server-url https://<서버 주소> \
     --only tables,policy,dist,grants,genie --apply
   ```

3. **App connection:** 위 "브라우저 로그인"의 1.
4. **이미지:** 저장소 최상단에서

   ```bash
   docker build -f deploy/container/Dockerfile -t aops-server .
   docker run --env-file aops.env -p 8000:8000 aops-server
   ```

   이미지에는 aops(serve·dbx), `databricks` CLI, agentbricks(코드 기반 운영 앱 배포용)가 들어 있고 root가 아닌 사용자로 돕니다(약 1.8GB, 대부분 dbx 의존성). 콘솔 화면(`/`)도 같은 서버가 내보냅니다. 상태 확인은 `GET /api/v1/meta/health`입니다.
   - pypi.org에 바로 나갈 수 없는 곳(사내 패키지 미러)에서는 `--build-arg PIP_INDEX_URL=https://<미러>/simple`을 줍니다. pip과 agentbricks 설치(uv)가 함께 씁니다. 실행 중인 이미지에는 남지 않습니다.
5. **HTTPS:** 로드 밸런서·인그레스에서 TLS를 끝내고 `AOPS_PUBLIC_URL`로 엽니다.
6. **사람·노트북:** PC CLI는 `AOPS_SERVER=https://<서버 주소>`(또는 `aops.toml [dbx] server`). 노트북·Genie Code가 쓰려면 Databricks 서버리스에서 이 주소에 닿아야 합니다(네트워크 정책).

## 내 PC에서 시험

- **혼자:** `AOPS_PROFILE=<내 프로필> AOPS_REGISTRY=… AOPS_WAREHOUSE_ID=… aops serve`. 로그인 없이 같은 PC(127.0.0.1)의 브라우저를 프로필 사용자로 봅니다. 외부에서 열면 이 방식은 꺼집니다.
- **로그인까지:** 위에 `AOPS_OAUTH_CLIENT_ID`·`AOPS_PUBLIC_URL=http://localhost:8000`·`AOPS_SESSION_KEY`를 더하면 서버는 프로필로, 사람은 OAuth 로그인으로 들어옵니다(리다이렉트 URL `http://localhost:8000/auth/callback`).

## 지금

- **동작:** 로그인·세션·갱신·로그아웃·CSRF 확인, 노트북 토큰, `admin setup --service-principal`·`--server-url`, Dockerfile이 동작합니다.
- **테스트:** 가짜 OAuth 서버로 시험했습니다(PKCE·state·Basic 인증·갱신·새 refresh token·만료).
- **컨테이너 실측 (시험 워크스페이스):** 이미지를 빌드해 띄웠습니다.
  - 서버는 자기 서비스 주체(OAuth M2M)로 돌고, `admin setup --service-principal`로 준 권한으로 레지스트리를 읽고 씁니다.
  - Bearer API(`/meta/me`·에이전트 목록)와 PC CLI(`AOPS_SERVER`)가 됩니다.
  - 브라우저: 계정의 실제 App connection으로 Databricks(SSO) 로그인 → 콘솔(목록·상세·정책, 내 토큰으로 내 폴더에 만들기, 서비스 주체로 등록) → 재시작 뒤에도 세션 유지 → 로그아웃.
- **아직:** 여러 복제본 앞의 로드 밸런서·HTTPS 구성은 조직 환경에서 시험합니다.

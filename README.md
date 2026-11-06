# aops

Databricks 위에서 AI 에이전트를 개발부터 운영까지 관리하는 오픈소스 도구입니다. Claude Code·Codex 같은 하네스로 에이전트를 만들고, Databricks 기능으로 기록·검증·배포·운영합니다.

![aops 아키텍처](docs/architecture.png)

## 주요 기능

- **에이전트 3종을 하나로:** 스킬 기반(누구나), 코드 기반(AI 개발자), 노코드(데이터 실무자, Genie Agent)를 같은 검증·레지스트리·배포·운영으로 관리합니다. 코드·노코드는 dbx 에디션부터 씁니다.
- **세션 이어가기:** 하네스를 바꿔도 대화와 메모리가 이어집니다.
- **구성별 비교:** 같은 질문을 하네스·모델·가드레일 구성별로 돌려 나란히 비교합니다.
- **평가·가드레일 시험:** MLflow로 채점하고, 레드팀 사례로 가드레일을 시험합니다.
- **검증 후 배포:** 등록·배포할 때만 관리자 정책을 검사하고, 승인된 버전만 배포합니다.
- **운영 모니터링:** 사용량·비용과 실행 중인 에이전트를 한 화면에서 봅니다.

## 핵심 명령어

```bash
aops init my-agent         # 프로젝트 만들기
aops dev                   # 개발 화면
aops run questions.yaml    # 구성별 비교 실행
aops evaluate              # 평가
aops validate              # 관리자 정책 검사
aops register              # 버전 등록
aops deploy                # 배포 요청 → 승인 → 배포
aops monitor               # 운영 현황
```

## 문서

- [개요](docs/overview.md): 단계별 Databricks 기능, 원칙, 에디션
- [명령어](docs/commands.md): 전체 명령과 옵션
- [프로젝트 폴더](docs/project.md): 폴더 구조와 `aops.toml`
- [레지스트리·승인](docs/registry.md): 관리자 정책, aops 서버, 테이블
- [API](docs/api.md): `aops serve`의 HTTP API와 명령 대응표

## 라이선스

[MIT](LICENSE)

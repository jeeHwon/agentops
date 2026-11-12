-- aops 레지스트리·승인 테이블 (확정 구성)
-- 세 종류 에이전트(스킬·코드·노코드)의 버전, 검사, 배포 심사·승인, 배포, 운영 포인터, 권한·가져오기 이력, 관리자 정책.
-- 값 규약: 열거형은 영문 소문자 코드(한글 라벨은 화면·뷰), 고정 구조는 STRUCT, 가변 구조는 VARIANT,
--          ID는 uuid4().hex, 해시는 sha256 16진수 64자리, 사람은 계정 이메일(서비스 주체는 application ID), 시각은 UTC.
-- 쓰기는 aops 서비스 주체(aops 서버, validate·배포 Job)만 한다. 사람에게는 조회 권한만 준다.
-- __SCHEMA__ 는 실행할 때 <catalog>.<schema> 로 바뀐다. 다시 실행해도 된다 (IF NOT EXISTS).
-- 모든 테이블은 catalog commits를 켠다. 여러 테이블을 BEGIN ATOMIC 한 트랜잭션으로 바꾸려면 필요하다.
-- PRIMARY KEY는 Unity Catalog 정보용 제약이라 강제되지 않는다. 허용 값은 CHECK로 강제한다.

CREATE TABLE IF NOT EXISTS __SCHEMA__.policies (
  policy_version INT NOT NULL COMMENT '정책 버전. 1부터 1씩 늘어난다',
  status STRING NOT NULL COMMENT '정책 상태. active(지금 적용, 하나만) | retired(이전 정책)',
  body VARIANT NOT NULL COMMENT '정책 내용. 검증 방식(validation_mode), 승인 방식(approval_mode), 승인 그룹, 보안 등급 등',
  created_by STRING NOT NULL COMMENT '정책을 올린 관리자',
  created_at TIMESTAMP NOT NULL COMMENT '정책을 올린 시각 (UTC)',
  CONSTRAINT policies_pk PRIMARY KEY (policy_version)
)
COMMENT '관리자 정책(배포 형식). aops admin policy가 새 버전을 추가하고, aops 서버·validate·배포 Job이 active 정책을 읽는다'
TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported');

CREATE TABLE IF NOT EXISTS __SCHEMA__.agents (
  agent_id STRING NOT NULL COMMENT '에이전트 ID. 소문자·숫자·하이픈 (SPEC agent_id)',
  name STRING NOT NULL COMMENT '표시 이름',
  description STRING COMMENT '한 줄 설명',
  build_type STRING NOT NULL COMMENT '종류. skill | code | no_code',
  owner STRING NOT NULL COMMENT '담당자. 비어 있으면 처음 등록한 사람',
  owner_group STRING COMMENT '담당 팀 (계정 그룹)',
  service_criticality STRING NOT NULL COMMENT '서비스 중요도. low | medium | high',
  data_sensitivity STRING NOT NULL COMMENT '데이터 민감도. public | internal | confidential | personal',
  security_level STRING NOT NULL COMMENT '보안 등급. 관리자 정책의 등급 목록 코드 (기본 general | important | secret)',
  lifecycle STRING NOT NULL COMMENT '수명 단계. registered(등록됨) | production(운영 중) | archived(보관)',
  spec VARIANT COMMENT '마지막 등록 때의 SPEC.md 항목',
  created_by STRING NOT NULL COMMENT '처음 등록한 사람',
  created_at TIMESTAMP NOT NULL COMMENT '처음 등록한 시각 (UTC)',
  updated_at TIMESTAMP NOT NULL COMMENT '마지막으로 바뀐 시각 (UTC)',
  CONSTRAINT agents_pk PRIMARY KEY (agent_id)
)
COMMENT '에이전트 마스터. 첫 register(노코드는 첫 배포)가 만들고 deploy가 운영 상태를 바꾼다'
TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported');

CREATE TABLE IF NOT EXISTS __SCHEMA__.genie_agents (
  agent_id STRING NOT NULL COMMENT '에이전트 ID',
  dev_space_id STRING COMMENT '개발용 Genie Agent의 space ID',
  prod_space_id STRING COMMENT '운영용 Genie Agent의 space ID (첫 배포 때 채운다)',
  folder_path STRING COMMENT 'aops가 만든 에이전트 폴더 (서비스 주체 권한이 이 폴더에서 상속된다)',
  consumer_group STRING COMMENT '공개 그룹 (배포 때 CAN RUN을 준다)',
  warehouse_id STRING COMMENT 'Genie Agent가 쓰는 SQL warehouse',
  updated_at TIMESTAMP NOT NULL COMMENT '마지막으로 바뀐 시각 (UTC)',
  CONSTRAINT genie_agents_pk PRIMARY KEY (agent_id)
)
COMMENT '노코드(Genie Agent) 에이전트의 개발용·운영용 스페이스와 공개 그룹'
TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported');

CREATE TABLE IF NOT EXISTS __SCHEMA__.agent_versions (
  agent_id STRING NOT NULL COMMENT '에이전트 ID',
  version STRING NOT NULL COMMENT '버전 (최초 1.0.0)',
  status STRING NOT NULL COMMENT '버전 상태. registered | production(지금 운영) | retired(이전 운영)',
  source_sha256 STRING COMMENT '버전 zip 안 파일 내용의 해시 (고등급은 NULL)',
  artifact_path STRING COMMENT '에이전트 볼륨의 버전 zip 경로 (/Volumes/.../<agent_id>/<version>.zip)',
  size_bytes BIGINT COMMENT '버전 zip 크기',
  skills_sha256 STRING COMMENT '스킬 통합 해시 (별칭:해시 줄을 별칭 순으로)',
  harness STRING COMMENT '기본 하네스 (claude | codex)',
  uc_model_version INT COMMENT 'UC registered model 버전 (코드 에이전트)',
  spec VARIANT COMMENT '이 버전의 SPEC.md 항목',
  registered_by STRING NOT NULL COMMENT '등록한 사람',
  registered_at TIMESTAMP NOT NULL COMMENT '등록한 시각 (UTC)',
  CONSTRAINT agent_versions_pk PRIMARY KEY (agent_id, version)
)
COMMENT '저장된 버전. 등록된 버전의 내용은 바꾸지 않는다 (zip은 한 번만 쓴다)'
TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported');

CREATE TABLE IF NOT EXISTS __SCHEMA__.agent_version_skills (
  agent_id STRING NOT NULL COMMENT '에이전트 ID',
  version STRING NOT NULL COMMENT '버전',
  skill_alias STRING NOT NULL COMMENT '에이전트 안의 스킬 이름 (폴더 이름)',
  uc_name STRING COMMENT 'Unity Gateway Skills 이름 (<catalog>.<schema>.<agent_id>__<별칭>). 게시를 건너뛰면 NULL',
  skill_sha256 STRING NOT NULL COMMENT '스킬 폴더 해시',
  source STRING NOT NULL COMMENT '원천 (에이전트 안 경로 또는 공용 스킬 UC 이름)',
  publication_mode STRING NOT NULL COMMENT 'agent(에이전트 전용) | shared_reference(공용 스킬 참조)',
  CONSTRAINT agent_version_skills_pk PRIMARY KEY (agent_id, version, skill_alias)
)
COMMENT '버전별 스킬과 UC Skill 이름·해시'
TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported');

CREATE TABLE IF NOT EXISTS __SCHEMA__.validation_runs (
  validation_run_id STRING NOT NULL COMMENT '검사 실행 ID',
  agent_id STRING NOT NULL COMMENT '에이전트 ID',
  version STRING COMMENT '검사한 버전 (노코드는 배포 때 정해져 NULL일 수 있다)',
  source_sha256 STRING COMMENT '검사한 내용의 해시',
  policy_version INT NOT NULL COMMENT '검사에 쓴 정책 버전',
  mode STRING NOT NULL COMMENT 'job(조직 검사, 배포 근거) | local(참고용)',
  status STRING NOT NULL COMMENT 'queued | running | passed | failed | stale(점검 중 정의가 바뀜) | error',
  steps VARIANT COMMENT '단계별 결과 [{name, status, detail}]',
  checks VARIANT COMMENT '판정 근거 (규칙별 결과, 점수 등)',
  failure_reason STRING COMMENT '실패·오류 사유',
  job_run_id BIGINT COMMENT 'validate Job 실행 ID',
  started_by STRING NOT NULL COMMENT '검사를 요청한 사람',
  started_at TIMESTAMP NOT NULL COMMENT '시작 시각 (UTC)',
  ended_at TIMESTAMP COMMENT '끝난 시각 (UTC)',
  CONSTRAINT validation_runs_pk PRIMARY KEY (validation_run_id)
)
COMMENT 'validate Job의 검사 실행 (Gate Self Check). 배포 요청이 같은 버전·정책의 passed 행을 요구한다'
TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported');

CREATE TABLE IF NOT EXISTS __SCHEMA__.eval_results (
  validation_run_id STRING NOT NULL COMMENT '검사 실행 ID',
  agent_id STRING NOT NULL COMMENT '에이전트 ID',
  question_id STRING NOT NULL COMMENT '문항 ID (노코드는 벤치마크 문항 ID)',
  question STRING COMMENT '질문',
  assessment STRING COMMENT '판정 (GOOD | BAD | NEEDS_REVIEW 등)',
  reasons VARIANT COMMENT '판정 이유',
  expected STRING COMMENT '정답 (노코드는 정답 SQL)',
  generated STRING COMMENT '생성 결과 (노코드는 생성 SQL)',
  created_at TIMESTAMP NOT NULL COMMENT '기록 시각 (UTC)',
  CONSTRAINT eval_results_pk PRIMARY KEY (validation_run_id, question_id)
)
COMMENT '문항별 평가 결과. 운영 버전의 결과가 다음 검사의 회귀 비교 기준이 된다'
TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported');

CREATE TABLE IF NOT EXISTS __SCHEMA__.release_gates (
  gate_id STRING NOT NULL COMMENT '배포 심사 요청 ID',
  agent_id STRING NOT NULL COMMENT '에이전트 ID',
  version STRING COMMENT '배포할 버전 (노코드는 배포 성공 때 정해진다)',
  requester STRING NOT NULL COMMENT '요청한 사람',
  reason STRING COMMENT '요청 사유',
  requested_at TIMESTAMP NOT NULL COMMENT '요청 시각 (UTC)',
  upgrade_type STRING COMMENT '운영 버전 대비 변경 폭. initial | major | minor | patch | rollback',
  policy_version INT NOT NULL COMMENT '심사에 쓴 정책 버전',
  validation_policy_mode STRING NOT NULL COMMENT '정책의 검증 방식. mandatory | optional | exempt',
  validation_run_id STRING COMMENT '근거 검사 실행',
  approval_mode STRING NOT NULL COMMENT '정책의 승인 방식. required | optional | auto',
  approval_channel STRING NOT NULL COMMENT '결재 채널. console | external',
  required_approvals INT NOT NULL COMMENT '필요한 승인 수',
  itsm_ticket_id STRING COMMENT '외부 결재 티켓 번호 (외부 결재 확장)',
  itsm_ticket_url STRING COMMENT '외부 결재 티켓 주소',
  decision STRING NOT NULL COMMENT '심사 상태. validating | pending_approval | approved | rejected | canceled',
  decision_reason STRING COMMENT '반려 사유 등',
  deployment_id STRING COMMENT '승인 뒤 배포 Job이 만든 배포',
  updated_at TIMESTAMP NOT NULL COMMENT '마지막으로 바뀐 시각 (UTC)',
  CONSTRAINT release_gates_pk PRIMARY KEY (gate_id)
)
COMMENT '배포 심사 요청 (aops deploy). Gate(검사)와 승인을 거쳐 배포 Job으로 이어진다'
TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported');

CREATE TABLE IF NOT EXISTS __SCHEMA__.approvals (
  approval_id STRING NOT NULL COMMENT '결재 ID',
  gate_id STRING NOT NULL COMMENT '배포 심사 요청 ID',
  approver STRING NOT NULL COMMENT '결재한 사람. 정책의 자동 승인은 policy:auto',
  decision STRING NOT NULL COMMENT 'approve | reject',
  comment STRING COMMENT '의견',
  channel STRING NOT NULL COMMENT 'console | external | policy',
  decided_at TIMESTAMP NOT NULL COMMENT '결재 시각 (UTC)',
  CONSTRAINT approvals_pk PRIMARY KEY (approval_id)
)
COMMENT '결재 이력. 추가만 하고 고치지 않는다'
TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported');

CREATE TABLE IF NOT EXISTS __SCHEMA__.deployments (
  deployment_id STRING NOT NULL COMMENT '배포 ID',
  gate_id STRING COMMENT '배포 심사 요청 ID (개인 시험 배포는 NULL)',
  agent_id STRING NOT NULL COMMENT '에이전트 ID',
  version STRING NOT NULL COMMENT '배포한 버전',
  environment STRING NOT NULL COMMENT 'dev(개인 시험 배포) | production(운영 반영)',
  target STRING COMMENT '반영한 대상 (앱 이름, 운영용 Genie space ID, UC Skill 이름 목록)',
  app_url STRING COMMENT '앱 주소 (코드 에이전트)',
  status STRING NOT NULL COMMENT 'running | succeeded | failed',
  job_run_id BIGINT COMMENT '배포 Job 실행 ID',
  detail VARIANT COMMENT '배포 결과 세부 (바뀐 파일, 의존 대조, 확인 결과)',
  started_at TIMESTAMP NOT NULL COMMENT '시작 시각 (UTC)',
  ended_at TIMESTAMP COMMENT '끝난 시각 (UTC)',
  CONSTRAINT deployments_pk PRIMARY KEY (deployment_id)
)
COMMENT '배포 결과. 배포 Job이 쓴다'
TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported');

CREATE TABLE IF NOT EXISTS __SCHEMA__.agent_production (
  agent_id STRING NOT NULL COMMENT '에이전트 ID',
  current_version STRING NOT NULL COMMENT '지금 운영 버전',
  previous_version STRING COMMENT '직전 운영 버전 (되돌리기 기준)',
  target STRING COMMENT '운영 대상 (앱 이름, 운영용 Genie space ID, UC Skill 이름 목록)',
  updated_by STRING NOT NULL COMMENT '배포를 요청한 사람',
  updated_at TIMESTAMP NOT NULL COMMENT '바뀐 시각 (UTC)',
  CONSTRAINT agent_production_pk PRIMARY KEY (agent_id)
)
COMMENT '운영 포인터. 에이전트당 1행. 배포 Job이 바꾼다'
TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported');

CREATE TABLE IF NOT EXISTS __SCHEMA__.agent_version_git (
  agent_id STRING NOT NULL COMMENT '에이전트 ID',
  version STRING NOT NULL COMMENT '버전',
  repo STRING NOT NULL COMMENT '저장소 (<조직>/<저장소 이름>)',
  url STRING COMMENT '이 버전 태그의 웹 주소 (synced일 때)',
  commit_sha STRING COMMENT '이 버전의 커밋 (synced일 때)',
  tag STRING COMMENT '태그 (v<버전>)',
  status STRING NOT NULL COMMENT 'synced(올림) | failed(실패, 다음 register·deploy 때 다시) | skipped(비밀값이 있어 올리지 않음)',
  error STRING COMMENT '실패하거나 건너뛴 이유',
  updated_at TIMESTAMP NOT NULL COMMENT '마지막으로 바뀐 시각 (UTC)',
  CONSTRAINT agent_version_git_pk PRIMARY KEY (agent_id, version)
)
COMMENT 'Git 사본(선택): 버전마다 에이전트 저장소의 커밋·태그. 정본은 볼륨의 버전 zip이다. 정책에 git이 있을 때만 쓴다'
TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported');

CREATE TABLE IF NOT EXISTS __SCHEMA__.agent_events (
  event_id STRING NOT NULL COMMENT '이벤트 ID',
  agent_id STRING NOT NULL COMMENT '에이전트 ID',
  version STRING COMMENT '관련 버전',
  event_type STRING NOT NULL COMMENT 'create(노코드 만들기) | register | validate | gate_requested | gate_approved | gate_rejected | gate_canceled | deploy | rollback | promote | load | git',
  actor STRING NOT NULL COMMENT '한 사람 (정책 자동 승인은 policy:auto)',
  at TIMESTAMP NOT NULL COMMENT '시각 (UTC)',
  detail VARIANT COMMENT '세부 (권한 대상, 배포 대상 등)',
  CONSTRAINT agent_events_pk PRIMARY KEY (event_id)
)
COMMENT '수명주기 감사 로그. 단계마다 한 행을 남긴다'
TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported');

CREATE TABLE IF NOT EXISTS __SCHEMA__.rate_cards (
  model STRING NOT NULL COMMENT '모델 또는 Model Service 이름',
  input_per_mtok DOUBLE NOT NULL COMMENT '입력 100만 토큰당 단가',
  output_per_mtok DOUBLE NOT NULL COMMENT '출력 100만 토큰당 단가',
  currency STRING NOT NULL COMMENT '통화 (예: USD)',
  valid_from DATE NOT NULL COMMENT '적용 시작일',
  valid_to DATE COMMENT '적용 종료일 (NULL이면 지금도 적용)',
  CONSTRAINT rate_cards_pk PRIMARY KEY (model, valid_from)
)
COMMENT '모델별 단가 이력. 추정 비용(trace 토큰 × 단가)에 쓴다'
TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported');

-- 허용 값 (CHECK). 테이블을 만든 뒤 붙인다 (CREATE TABLE 안에는 쓸 수 없다)
ALTER TABLE __SCHEMA__.policies DROP CONSTRAINT IF EXISTS policies_status;
ALTER TABLE __SCHEMA__.policies ADD CONSTRAINT policies_status CHECK (status IN ('active', 'retired'));
ALTER TABLE __SCHEMA__.agents DROP CONSTRAINT IF EXISTS agents_build_type;
ALTER TABLE __SCHEMA__.agents ADD CONSTRAINT agents_build_type CHECK (build_type IN ('skill', 'code', 'no_code'));
ALTER TABLE __SCHEMA__.agents DROP CONSTRAINT IF EXISTS agents_criticality;
ALTER TABLE __SCHEMA__.agents ADD CONSTRAINT agents_criticality CHECK (service_criticality IN ('low', 'medium', 'high'));
ALTER TABLE __SCHEMA__.agents DROP CONSTRAINT IF EXISTS agents_sensitivity;
ALTER TABLE __SCHEMA__.agents ADD CONSTRAINT agents_sensitivity CHECK (data_sensitivity IN ('public', 'internal', 'confidential', 'personal'));
ALTER TABLE __SCHEMA__.agents DROP CONSTRAINT IF EXISTS agents_lifecycle;
ALTER TABLE __SCHEMA__.agents ADD CONSTRAINT agents_lifecycle CHECK (lifecycle IN ('registered', 'production', 'archived'));
ALTER TABLE __SCHEMA__.agent_versions DROP CONSTRAINT IF EXISTS agent_versions_status;
ALTER TABLE __SCHEMA__.agent_versions ADD CONSTRAINT agent_versions_status CHECK (status IN ('registered', 'production', 'retired'));
ALTER TABLE __SCHEMA__.agent_version_skills DROP CONSTRAINT IF EXISTS agent_version_skills_mode;
ALTER TABLE __SCHEMA__.agent_version_skills ADD CONSTRAINT agent_version_skills_mode CHECK (publication_mode IN ('agent', 'shared_reference'));
ALTER TABLE __SCHEMA__.validation_runs DROP CONSTRAINT IF EXISTS validation_runs_mode;
ALTER TABLE __SCHEMA__.validation_runs ADD CONSTRAINT validation_runs_mode CHECK (mode IN ('job', 'local'));
ALTER TABLE __SCHEMA__.validation_runs DROP CONSTRAINT IF EXISTS validation_runs_status;
ALTER TABLE __SCHEMA__.validation_runs ADD CONSTRAINT validation_runs_status CHECK (status IN ('queued', 'running', 'passed', 'failed', 'stale', 'error'));
ALTER TABLE __SCHEMA__.release_gates DROP CONSTRAINT IF EXISTS release_gates_validation;
ALTER TABLE __SCHEMA__.release_gates ADD CONSTRAINT release_gates_validation CHECK (validation_policy_mode IN ('mandatory', 'optional', 'exempt'));
ALTER TABLE __SCHEMA__.release_gates DROP CONSTRAINT IF EXISTS release_gates_approval;
ALTER TABLE __SCHEMA__.release_gates ADD CONSTRAINT release_gates_approval CHECK (approval_mode IN ('required', 'optional', 'auto'));
ALTER TABLE __SCHEMA__.release_gates DROP CONSTRAINT IF EXISTS release_gates_channel;
ALTER TABLE __SCHEMA__.release_gates ADD CONSTRAINT release_gates_channel CHECK (approval_channel IN ('console', 'external'));
ALTER TABLE __SCHEMA__.release_gates DROP CONSTRAINT IF EXISTS release_gates_decision;
ALTER TABLE __SCHEMA__.release_gates ADD CONSTRAINT release_gates_decision CHECK (decision IN ('validating', 'pending_approval', 'approved', 'rejected', 'canceled'));
ALTER TABLE __SCHEMA__.approvals DROP CONSTRAINT IF EXISTS approvals_decision;
ALTER TABLE __SCHEMA__.approvals ADD CONSTRAINT approvals_decision CHECK (decision IN ('approve', 'reject'));
ALTER TABLE __SCHEMA__.approvals DROP CONSTRAINT IF EXISTS approvals_channel;
ALTER TABLE __SCHEMA__.approvals ADD CONSTRAINT approvals_channel CHECK (channel IN ('console', 'external', 'policy'));
ALTER TABLE __SCHEMA__.deployments DROP CONSTRAINT IF EXISTS deployments_environment;
ALTER TABLE __SCHEMA__.deployments ADD CONSTRAINT deployments_environment CHECK (environment IN ('dev', 'production'));
ALTER TABLE __SCHEMA__.deployments DROP CONSTRAINT IF EXISTS deployments_status;
ALTER TABLE __SCHEMA__.deployments ADD CONSTRAINT deployments_status CHECK (status IN ('running', 'succeeded', 'failed'));
ALTER TABLE __SCHEMA__.agent_events DROP CONSTRAINT IF EXISTS agent_events_type;
ALTER TABLE __SCHEMA__.agent_events ADD CONSTRAINT agent_events_type CHECK (event_type IN ('create', 'register', 'validate', 'gate_requested', 'gate_approved', 'gate_rejected', 'gate_canceled', 'deploy', 'rollback', 'promote', 'load', 'git'));
ALTER TABLE __SCHEMA__.agent_version_git DROP CONSTRAINT IF EXISTS agent_version_git_status;
ALTER TABLE __SCHEMA__.agent_version_git ADD CONSTRAINT agent_version_git_status CHECK (status IN ('synced', 'failed', 'skipped'));

CREATE OR REPLACE VIEW __SCHEMA__.agent_status
COMMENT 'console 레지스트리 화면과 aops list가 쓰는 에이전트별 현황'
AS
SELECT
  a.agent_id, a.name, a.description, a.build_type, a.owner, a.owner_group,
  a.service_criticality, a.data_sensitivity, a.security_level, a.lifecycle,
  (SELECT max_by(v.version, v.registered_at) FROM __SCHEMA__.agent_versions v WHERE v.agent_id = a.agent_id) AS latest_version,
  p.current_version AS production_version,
  (SELECT max_by(r.status, r.started_at) FROM __SCHEMA__.validation_runs r WHERE r.agent_id = a.agent_id) AS last_validation,
  (SELECT count(*) FROM __SCHEMA__.release_gates g WHERE g.agent_id = a.agent_id AND g.decision IN ('validating', 'pending_approval')) AS pending_gates,
  a.updated_at
FROM __SCHEMA__.agents a
LEFT JOIN __SCHEMA__.agent_production p ON p.agent_id = a.agent_id;

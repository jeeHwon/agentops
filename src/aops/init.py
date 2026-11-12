"""`aops init`: a new agent folder from the skill template."""

from __future__ import annotations

import re
from pathlib import Path

from .project import CONFIG_FILE, Project
from .sync import sync

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

AOPS_TOML = """\
config_version = 1

[project]
name = "{name}"
edition = "{edition}"              # local | dbx | omni | custom

[agent]
harness = "{harness}"              # 새 세션의 기본 하네스 (claude | codex). 세션 중 바꾸기: aops session switch
instructions = "agent/instructions.md"
skills = ["agent/skills/*"]
user_settings = false              # true면 aops run(Claude)이 개인 설정(~/.claude의 MCP·hook·CLAUDE.md)도 읽음

[session]
handoff_tokens = {{ codex = 2000, claude = 4000 }}
"""

SPEC_DESCRIPTION = "이 에이전트가 하는 일을 한 줄로 적습니다 (에이전트 목록에 보입니다)"
SKILL_DESCRIPTION = "이 스킬을 언제 쓰는지 한 줄로 적습니다. 하네스는 이 설명을 보고 스킬을 고릅니다."

DBX_TOML = """
[dbx]
profile = ""                   # Databricks CLI 프로필. 환경변수 AOPS_PROFILE이 있으면 그것을 씀
server = ""                    # aops 서버 주소 (https://<app>.databricksapps.com). 환경변수 AOPS_SERVER가 있으면 그것을 씀
trace = true                   # aops run마다 개발 trace를 MLflow 실험에 남김 (false면 끔)
experiment = ""                # trace를 남길 MLflow 실험 (비우면 /Users/<나>/aops/<이름>)
"""

SPEC_MD = """\
---
agent_id: {name}
name: {name}
build_type: skill
owner:
owner_group:
service_criticality: medium    # low | medium | high
data_sensitivity: internal     # public | internal | confidential | personal
security_level: general
description: {spec_description}
---

# {name}

이 에이전트가 하는 일, 쓰는 사람, 주의할 점을 적습니다.
"""

SPEC_KEYS = ("agent_id", "name", "build_type", "owner", "owner_group", "service_criticality", "data_sensitivity",
             "security_level", "consumer_group", "description")
SPEC_HINTS = {"service_criticality": "low | medium | high", "data_sensitivity": "public | internal | confidential | personal",
              "consumer_group": "공개 그룹 (노코드: 배포 때 운영 Genie에 CAN RUN)"}


def render_spec(values: dict[str, str | None], body: str | None = None) -> str:
    """SPEC.md from the fields a screen asks for (one line each; the front matter reader takes `key: value`)."""
    lines = ["---"]
    for key in SPEC_KEYS:
        if key == "consumer_group" and values.get("build_type") != "no_code" and not values.get(key):
            continue
        value = " ".join(str(values.get(key) or "").split()).replace(" #", " ＃")  # " #" would start a comment
        hint = SPEC_HINTS.get(key)
        lines.append(f"{key}: {value}" + (f"    # {hint}" if hint else ""))
    name = values.get("name") or values.get("agent_id") or ""
    lines += ["---", "", f"# {name}", "", body or "이 에이전트가 하는 일, 쓰는 사람, 주의할 점을 적습니다.", ""]
    return "\n".join(lines)


INSTRUCTIONS_MD = """\
당신은 {name} 에이전트입니다.

- 질문에 맞는 스킬이 있으면 그 스킬의 규칙을 따릅니다.
- 답은 한국어로 짧고 정확하게 합니다.
"""

TEMPLATE_SKILL = "my-skill"

SKILL_MD = """\
---
name: my-skill
description: {skill_description}
---

# my-skill (스킬 템플릿)

폴더 이름과 위 `name`을 같은 스킬 이름으로 바꾸고(소문자·숫자·하이픈), 아래에 규칙을 적습니다.
스킬을 더 만들려면 `agent/skills/<이름>/SKILL.md`를 같은 형식으로 추가합니다.

1. 지켜야 할 규칙이나 계산 순서
2. 답의 형식
"""

QUESTIONS_YAML = """\
# 이 에이전트가 잘 답해야 하는 질문 (평가·비교에 씁니다)
questions:
  - id: q1
    text: 여기에 질문을 적습니다
"""

README_MD = """\
# {name}

aops로 만든 스킬 기반 에이전트입니다. **원본만 고치면** Claude Code·Codex 설정은 aops가 만듭니다.

## 폴더 구조

```text
{name}/
├── README.md                 [안내] 이 파일
├── SPEC.md                   [고침] 에이전트 설명서
├── aops.toml                 [고침] aops 설정 (필요할 때)
├── agent/
│   ├── instructions.md       [고침] 지시: 역할, 답하는 방식
│   └── skills/
│       └── my-skill/
│           └── SKILL.md      [고침] 스킬 템플릿: 폴더 이름과 내용을 내 스킬로
├── evals/
│   └── questions.yaml        [고침] 평가 질문
├── CLAUDE.md, AGENTS.md      [aops] Claude Code·Codex용 지시
├── .claude/skills/           [aops] Claude Code용 스킬 사본
├── .agents/skills/           [aops] Codex용 스킬 사본
└── .aops/, runs/             [aops] 세션·대화 기록
```

[고침] 사람이 고치는 원본 · [aops] aops가 원본에서 만드는 것 (고치지 않음)

`aops run`을 하면 실행 직전에 aops가 원본(`agent/instructions.md`, `agent/skills/`)에서
`CLAUDE.md`·`AGENTS.md`와 스킬 사본을 다시 맞춥니다. 사본을 손으로 고치면 aops가 덮어쓰지 않고 알려 줍니다.

## 고칠 곳

1. `SPEC.md` — `description`(한 줄 설명), `owner`·`owner_group`(담당자·담당 팀, 비우면 등록한 사람),
   `service_criticality`·`data_sensitivity`·`security_level`(승인 정책이 보는 값)
2. `agent/instructions.md` — 이 에이전트의 역할과 답하는 방식. Claude·Codex가 같이 받습니다
3. `agent/skills/my-skill/` — 폴더 이름을 스킬 이름으로 바꾸고(소문자·숫자·하이픈), `SKILL.md`의
   `name`(폴더 이름과 같게)·`description`(언제 쓰는지)·본문(규칙)을 채웁니다.
   스킬을 더 만들려면 `agent/skills/<이름>/SKILL.md`를 같은 형식으로 추가합니다 ([Agent Skills](https://agentskills.io) 형식)
4. `evals/questions.yaml` — 이 에이전트가 잘 답해야 하는 질문

## 쓰는 법

```bash
aops run "<질문>"                      # 실행
aops session switch --harness codex    # 다음 턴부터 다른 하네스 (대화는 이어짐)
aops session show                      # 이 세션의 기록 (하네스 구간, 쓴 스킬)
{dbx_usage}```
"""

DBX_USAGE = """\
aops register                          # 버전 저장 (aops 서버, 볼륨·UC Skill)
aops deploy                            # 검사 → 승인 → 운영 반영
aops list                              # 등록된 에이전트 목록
"""

GITIGNORE = "runs/\n.aops/\n"


CODE_TOML = """config_version = 1

[project]
name = "{name}"
edition = "dbx"
type = "code"                  # 코드 기반 (Agent Bricks 프로젝트). aops run 대신 agentbricks dev로 시험
"""

CODE_README_NOTE = """
## aops (코드 기반)

이 폴더는 Agent Bricks 프로젝트에 aops 파일(`aops.toml`, `SPEC.md`)을 더한 것입니다.

```bash
agentbricks dev                        # 내 PC에서 시험 (http://localhost:8000)
aops register                          # 버전 저장 (aops 서버)
aops deploy                            # 검사 → 승인 → aops 서버가 운영 앱(agent-bricks-<이름>)에 반영
```

운영 배포는 aops 서버가 합니다. `agentbricks deploy`를 직접 실행하면 개인 시험 앱이 생깁니다.
"""


def create_code(parent: Path, name: str, *, framework: str = "openai", agentbricks: str = "agentbricks") -> Project:
    """A code-based agent: `agentbricks init` (the Agent Bricks template) plus aops.toml and SPEC.md."""
    import os
    import subprocess

    if not NAME.match(name):
        raise ValueError("name must be lowercase letters, digits and hyphens (up to 63 characters)")
    root = parent / name
    env = {**os.environ, "AGENTBRICKS_DISABLE_TELEMETRY": "1", "AGENTBRICKS_DISABLE_AGENT_HINT": "1"}
    done = subprocess.run([agentbricks, "init", str(root), "--framework", framework], capture_output=True, text=True, env=env)
    if done.returncode != 0 or not (root / "agent.toml").exists():
        raise RuntimeError(f"agentbricks init 실패: {(done.stderr or done.stdout)[-400:]}")
    spec = SPEC_MD.format(name=name, spec_description=SPEC_DESCRIPTION).replace("build_type: skill", "build_type: code")
    (root / CONFIG_FILE).write_text(CODE_TOML.format(name=name) + DBX_TOML, encoding="utf-8")
    (root / "SPEC.md").write_text(spec, encoding="utf-8")
    readme = root / "README.md"
    readme.write_text((readme.read_text(encoding="utf-8") if readme.exists() else f"# {name}\n") + CODE_README_NOTE, encoding="utf-8")
    return Project.load(root)


def create(parent: Path, name: str, *, harness: str = "claude", edition: str = "local") -> Project:
    if not NAME.match(name):
        raise ValueError("name must be lowercase letters, digits and hyphens (up to 63 characters)")
    root = parent / name
    if (root / CONFIG_FILE).exists():
        raise ValueError(f"{root} is already an aops project")
    files = {
        "README.md": README_MD.format(name=name, dbx_usage=DBX_USAGE if edition != "local" else ""),
        CONFIG_FILE: AOPS_TOML.format(name=name, harness=harness, edition=edition) + (DBX_TOML if edition != "local" else ""),
        "SPEC.md": SPEC_MD.format(name=name, spec_description=SPEC_DESCRIPTION),
        "agent/instructions.md": INSTRUCTIONS_MD.format(name=name),
        f"agent/skills/{TEMPLATE_SKILL}/SKILL.md": SKILL_MD.format(skill_description=SKILL_DESCRIPTION),
        "evals/questions.yaml": QUESTIONS_YAML,
        ".gitignore": GITIGNORE,
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    project = Project.load(root)
    sync(project)
    return project

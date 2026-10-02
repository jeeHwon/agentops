from __future__ import annotations

import hashlib
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

import yaml


AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


EXAMPLE_SKILL = """\
---
name: example-skill
description: Summarize a request and identify the key checks and next actions.
---

# Example Skill

## Purpose

Turn a user request into a concise, actionable summary.

## Inputs

- The user request
- Any context and constraints supplied by the user

## Procedure

1. Summarize the request in one sentence.
2. Identify only the checks required to complete the work.
3. List the next actions in priority order.

## Output

- Request summary
- Required checks
- Next actions

## Constraints

- Do not invent facts that were not provided.
- Do not expose credentials or sensitive information.
"""


class ManifestError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentManifest:
    root: Path
    agent_id: str
    name: str
    schema_version: int


@dataclass(frozen=True)
class DefinitionChecksums:
    harness: str
    skills: dict[str, str]
    combined_skills: str


def find_agent_root(start: str | Path) -> Path | None:
    path = Path(start).expanduser().resolve()
    if path.is_file():
        path = path.parent
    for candidate in (path, *path.parents):
        if (candidate / "agent.yaml").is_file():
            return candidate
    return None


def load_manifest(root: str | Path) -> AgentManifest:
    agent_root = Path(root).expanduser().resolve()
    target = agent_root / "agent.yaml"
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError(f"Missing manifest: {target}") from exc
    except (OSError, yaml.YAMLError) as exc:
        raise ManifestError(f"Invalid manifest {target}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError(f"Manifest {target} must be a YAML object.")
    schema_version = raw.get("schema_version")
    agent_id = str(raw.get("agent_id", "")).strip()
    name = str(raw.get("name", "")).strip()
    if schema_version != 1:
        raise ManifestError("agent.yaml schema_version must be 1.")
    if not AGENT_ID_RE.fullmatch(agent_id):
        raise ManifestError("agent_id must use lower-case kebab-case.")
    if not name:
        raise ManifestError("agent.yaml name is required.")
    return AgentManifest(agent_root, agent_id, name, schema_version)


def validate_agent(root: str | Path) -> AgentManifest:
    manifest = load_manifest(root)
    skills_root = manifest.root / ".agents" / "skills"
    skill_dirs = sorted(path for path in skills_root.glob("*") if path.is_dir())
    if not skill_dirs:
        raise ManifestError(f"At least one local Skill is required under {skills_root}.")
    for skill_dir in skill_dirs:
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.is_file():
            raise ManifestError(f"Missing Skill entrypoint: {skill_file}")
        _validate_skill(skill_file)
    return manifest


def create_agent(agent_id: str, destination: str | Path | None = None) -> Path:
    if not AGENT_ID_RE.fullmatch(agent_id):
        raise ManifestError("agent_id must use lower-case kebab-case.")
    root = Path(destination or agent_id).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ManifestError(f"Destination is not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    skill_dir = root / ".agents" / "skills" / "example-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    files: dict[Path, str] = {
        root / "agent.yaml": (
            "schema_version: 1\n"
            f"agent_id: {agent_id}\n"
            f"name: {agent_id.replace('-', ' ').title()}\n"
        ),
        root / "README.md": _agent_readme(agent_id),
        root / "spec.md": _business_spec(),
        root / "AGENTS.md": (
            "# Agent Instructions\n\n"
            "## Shared behavior\n\n"
            "- Follow the scope and success criteria in `spec.md`.\n"
            "- Confirm the user's actual request before acting.\n"
            "- Do not invent unsupported facts.\n"
            "- Ask for more information only when it is required.\n\n"
            "## Skill selection\n\n"
            "- Use `example-skill` when the request matches its description.\n"
            "- Document selection rules and priority whenever a new Skill is added.\n"
        ),
        root / "CLAUDE.md": (
            "# Compatibility Instructions\n\n"
            "Keep this file only for compatibility with other harness runtimes.\n"
        ),
        skill_dir / "SKILL.md": EXAMPLE_SKILL,
    }
    for path, content in files.items():
        path.write_text(content, encoding="utf-8")
    return root


def _agent_readme(agent_id: str) -> str:
    title = agent_id.replace("-", " ").title()
    return textwrap.dedent(
        f"""\
        # {title}

        This is a standard Codex AgentOps harness-agent folder. Edit the business definition and Skills here; the installed plugin owns runtime and observability code.

        ## What to edit

        1. **Define the operating contract in `spec.md`.**
           - Keep `agent_type: harness` for this runtime.
           - Set service criticality, data sensitivity, access level, required Unity Catalog permissions, and model policy.
           - Replace the purpose, users, inputs, outputs, scope, and success criteria with the actual business requirements.

        2. **Define shared harness behavior in `AGENTS.md`.**
           - Describe the role, response rules, prohibited behavior, and how to handle uncertainty.
           - Define when to use each Skill and how to resolve overlapping Skills.
           - Keep detailed business procedures in `SKILL.md` files.

        3. **Implement procedures in `.agents/skills/<skill-name>/SKILL.md`.**
           - The frontmatter `name` must match the folder name and use kebab-case.
           - `description` must state what the Skill does and when to use it.
           - The body should define inputs, ordered steps, output format, and constraints.

        4. **Document examples and operating notes in this `README.md`.**

        5. **Edit `CLAUDE.md` only when another compatible harness runtime needs separate instructions.**

        Do not change `agent.yaml` after creation unless the registry identity is intentionally being migrated.

        ## Validate and run

        ```bash
        codex-agentops validate
        codex
        ```

        Review and trust the `codex-agentops` plugin once through `/hooks`. Test normal requests and invoke `$example-skill` directly when you need to test its procedure in isolation.

        In MLflow, inspect the `agent.turn` trace for the prompt, final response, subagent and tool spans, exact token usage, and background `RelevanceToQuery`, `Safety`, and `Completeness` feedback.

        Each turn uploads after `Stop` or `Interrupt`; the Codex session does not need to end. Use `codex-agentops status` and `codex-agentops flush` to inspect or retry queued telemetry.

        ## File responsibilities

        | File | Responsibility |
        |---|---|
        | `spec.md` | Business scope, risk classification, inputs, outputs, and success criteria |
        | `AGENTS.md` | Shared harness behavior and Skill selection rules |
        | `.agents/skills/<skill-name>/SKILL.md` | A specific business procedure and output contract |
        | `README.md` | Usage examples, known limitations, and operating notes |
        | `CLAUDE.md` | Optional compatibility instructions for another harness runtime |
        | `agent.yaml` | Stable agent identity |

        ## Add a Skill

        ```bash
        cp -R .agents/skills/example-skill .agents/skills/customer-summary
        ```

        Change the copied folder name, the `name` and `description` in `SKILL.md`, and the Skill selection rules in `AGENTS.md`.

        ```markdown
        ---
        name: customer-summary
        description: Summarize a customer conversation and identify follow-up actions.
        ---

        # Customer Summary

        ## Purpose
        Describe the business task solved by this Skill.

        ## Inputs
        - List required inputs and preconditions.

        ## Procedure
        1. Validate the inputs.
        2. Follow the business procedure in order.
        3. Return the result in the required format.

        ## Output
        - List every required output field.

        ## Constraints
        - State prohibited actions, security rules, and allowed data boundaries.
        ```

        ## Folder structure

        ```text
        {agent_id}/
        ├── agent.yaml
        ├── README.md
        ├── spec.md
        ├── AGENTS.md
        ├── CLAUDE.md
        └── .agents/skills/
            └── example-skill/
                └── SKILL.md
        ```
        """
    )


def _business_spec() -> str:
    return textwrap.dedent(
        """\
        ---
        agent_type: harness
        service_criticality: low
        data_sensitivity: internal
        security_access_level: standard
        required_uc_permissions: []
        use_flagship_model: false
        ---

        # Business Specification

        Replace these defaults with the real business and data requirements before production use.

        ## Classification

        | Field | Default | Allowed values or meaning |
        |---|---|---|
        | Agent type | `harness` | Keep `harness` for this runtime. |
        | Service criticality | `low` | `low`, `medium`, or `high`, based on the impact of failure or incorrect output. |
        | Data sensitivity | `internal` | `public`, `internal`, `confidential`, or `restricted`. |
        | Security access level | `standard` | `standard`, `elevated`, or `strict`. |
        | Required UC permissions | `[]` | Add each required Unity Catalog resource and privilege. |
        | Flagship model | `false` | Set to `true` only when the success criteria require it, and explain why below. |

        These values declare requirements; they do not grant permissions or security approval. The runtime must still enforce the authenticated user or deployment identity's actual permissions.

        Example Unity Catalog requirement:

        ```yaml
        required_uc_permissions:
          - resource: catalog.schema.table
            privileges: [SELECT]
            purpose: Read approved reference data required to answer the request.
        ```

        ## Purpose

        Process user requests through defined Skill procedures and return reviewable results.

        ## Users and inputs

        - Intended users: define the authorized user group.
        - Required inputs: define the request and supporting context.
        - Prohibited inputs: credentials, secrets, or unapproved sensitive data.

        ## Outputs

        - Final result
        - Supporting rationale and material limitations
        - Required next actions, when applicable

        ## Scope

        - Included: work defined in `.agents/skills` using approved tools and MCP servers.
        - Excluded: unauthorized data access and undefined changes to external systems.

        ## Success criteria

        - Satisfy the explicit request and every required output field.
        - Do not invent unsupported facts; label uncertainty clearly.
        - Pass the configured Safety evaluation and avoid exposing secrets or personal data.
        - Follow the selected Skill procedure and output format.
        - Add measurable, business-specific targets here.

        ## Flagship model justification

        The default is `false`. If changed to `true`, explain the quality threshold that a standard model cannot meet and the specific tasks that need the flagship model.
        """
    )


def _validate_skill(path: Path) -> None:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"Cannot read Skill {path}: {exc}") from exc
    if not content.startswith("---\n"):
        raise ManifestError(f"Skill must start with YAML frontmatter: {path}")
    parts = content.split("---", 2)
    if len(parts) != 3:
        raise ManifestError(f"Skill frontmatter is not closed: {path}")
    try:
        metadata = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        raise ManifestError(f"Invalid Skill frontmatter {path}: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ManifestError(f"Skill frontmatter must be a YAML object: {path}")
    name = str(metadata.get("name", "")).strip()
    if not AGENT_ID_RE.fullmatch(name):
        raise ManifestError(f"Skill name must use lower-case kebab-case: {path}")
    if name != path.parent.name:
        raise ManifestError(f"Skill name must match its folder '{path.parent.name}': {path}")
    if not str(metadata.get("description", "")).strip():
        raise ManifestError(f"Skill description is required: {path}")
    if not parts[2].strip():
        raise ManifestError(f"Skill instructions are required after frontmatter: {path}")


def calculate_definition_checksums(root: str | Path) -> DefinitionChecksums:
    agent_root = Path(root).expanduser().resolve()
    harness_files = ["README.md", "spec.md", "AGENTS.md", "CLAUDE.md"]
    harness = _hash_files(agent_root, [agent_root / name for name in harness_files])
    skill_files = sorted((agent_root / ".agents" / "skills").glob("*/SKILL.md"))
    skills = {
        str(path.relative_to(agent_root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in skill_files
    }
    combined = hashlib.sha256(
        "\n".join(f"{name}:{checksum}" for name, checksum in skills.items()).encode("utf-8")
    ).hexdigest()
    return DefinitionChecksums(harness, skills, combined)


def _hash_files(root: Path, files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()

from __future__ import annotations

from typing import Any

from .registry import AgentVersion, RegistryLocation


def catalog_record(release: AgentVersion, location: RegistryLocation) -> dict[str, Any]:
    """Public release metadata for a Delta-backed cross-runtime catalog."""
    spec = release.release_manifest.get("spec")
    if not isinstance(spec, dict):
        raise ValueError("Release is missing a SPEC.md snapshot.")
    skills = release.release_manifest.get("skills")
    if not isinstance(skills, list) or not skills:
        raise ValueError("Release is missing its UC Skill references.")
    skill_names = []
    for skill in skills:
        if not isinstance(skill, dict) or not isinstance(skill.get("uc_name"), str):
            raise ValueError("Release contains an invalid UC Skill reference.")
        skill_names.append(skill["uc_name"])
    return {
        "agent_id": release.agent_id,
        "version": release.version,
        "name": release.name,
        "description": release.description,
        "type": "skill",
        "source_registry": location.schema_name,
        "artifact_sha256": release.artifact_sha256,
        "uc_skills": skill_names,
        "spec": {
            key: spec.get(key)
            for key in (
                "agent_id", "agent_name", "agent_version", "build_type",
                "serving_type", "origin", "owner", "owner_group",
                "service_criticality", "data_sensitivity", "security_level",
                "uc_permissions", "success_criteria", "flagship_model",
                "model_service",
            )
        },
    }

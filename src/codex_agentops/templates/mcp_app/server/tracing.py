from __future__ import annotations

import os
from typing import Any

import mlflow
from databricks.sdk import WorkspaceClient
from databricks_openai import AsyncDatabricksOpenAI
from mlflow.entities import SpanType

from .context import request_headers
from .runtime import runtime


mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", "databricks"))
if experiment_id := os.environ.get("MLFLOW_EXPERIMENT_ID"):
    mlflow.set_experiment(experiment_id=experiment_id)
mlflow.openai.autolog()


def _user_client() -> AsyncDatabricksOpenAI:
    token = request_headers.get().get("x-forwarded-access-token")
    if not token:
        if os.environ.get("DATABRICKS_APP_NAME"):
            raise RuntimeError("OAuth/OBO access token is missing")
        return AsyncDatabricksOpenAI()
    workspace = WorkspaceClient(
        host=os.environ.get("DATABRICKS_HOST"),
        token=token,
        auth_type="pat",
    )
    return AsyncDatabricksOpenAI(workspace_client=workspace)


def _identity() -> str:
    headers = request_headers.get()
    return (
        headers.get("x-forwarded-user")
        or headers.get("x-forwarded-preferred-username")
        or headers.get("x-forwarded-email")
        or "local-user"
    )


def _usage_dict(usage: Any) -> dict[str, int]:
    if usage is None:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    values = usage.model_dump() if hasattr(usage, "model_dump") else {}
    return {
        "input_tokens": int(values.get("prompt_tokens", 0) or 0),
        "output_tokens": int(values.get("completion_tokens", 0) or 0),
        "total_tokens": int(values.get("total_tokens", 0) or 0),
    }


def _trace_metadata(skills: list[dict[str, str]]) -> dict[str, str]:
    metadata = {
        "agent.id": runtime.agent_id,
        "agent.release_version": runtime.release_version,
        "agent.skill_count": str(len(skills)),
        "agent.harness_profile": str(runtime.config["profiles"]["harness"]),
        "agent.model_profile": str(runtime.config["profiles"]["model"]),
        "agent.mcp_tool_profile": str(runtime.config["profiles"]["mcp_tools"]),
    }
    # MLflow trace metadata values are limited to 250 characters. Store each
    # release-pinned Skill field separately so a multi-Skill manifest is not
    # silently truncated. The root span also retains the complete structure.
    for index, skill in enumerate(skills):
        prefix = f"agent.skill.{index}"
        metadata[f"{prefix}.alias"] = skill["alias"]
        metadata[f"{prefix}.uc_name"] = skill["uc_name"]
        metadata[f"{prefix}.version"] = skill["version"]
        metadata[f"{prefix}.sha256"] = skill["sha256"]
    return metadata


@mlflow.trace(name="agent.ask", span_type=SpanType.AGENT)
async def ask(query: str, session_id: str | None = None) -> dict:
    user = _identity()
    skills = [
        {
            "alias": item["alias"],
            "uc_name": item["uc_name"],
            "version": item["version"],
            "sha256": item["sha256"],
        }
        for item in runtime.skills
    ]
    mlflow.update_current_trace(
        metadata=_trace_metadata(skills),
        user=user,
        session_id=session_id,
    )

    span = mlflow.get_current_active_span()
    if span:
        span.set_attribute("agent.id", runtime.agent_id)
        span.set_attribute("agent.release_version", runtime.release_version)
        span.set_attribute("agent.skills", skills)

    endpoint = os.environ["DATABRICKS_SERVING_ENDPOINT_NAME"]
    response = await _user_client().chat.completions.create(
        model=endpoint,
        messages=[
            {"role": "system", "content": runtime.system_prompt},
            {"role": "user", "content": query},
        ],
    )
    content = response.choices[0].message.content or ""
    usage = _usage_dict(response.usage)
    if span:
        span.set_attribute("token.input", usage["input_tokens"])
        span.set_attribute("token.output", usage["output_tokens"])
        span.set_attribute("token.total", usage["total_tokens"])
        span.set_attribute("model.endpoint", endpoint)
    return {
        "answer": content,
        "agent_id": runtime.agent_id,
        "release_version": runtime.release_version,
        "model_endpoint": endpoint,
        "usage": usage,
    }

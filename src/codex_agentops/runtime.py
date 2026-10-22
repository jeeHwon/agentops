from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from databricks.sdk import WorkspaceClient

from .config import Settings
from .manifest import ManifestError, validate_agent
from .project import load_genie_definition, load_project, resolve_project, save_project


class NativeRuntime:
    """Thin adapters around official Agent Bricks and Genie entry points."""

    def __init__(
        self, profile: str, *, workspace: Any = None, runner: Any = None,
        settings: Settings | None = None,
    ):
        if not profile.strip():
            raise ValueError("An explicitly selected Databricks profile is required.")
        self.profile = profile
        self.workspace = workspace or WorkspaceClient(profile=profile)
        self.runner = runner or self._run
        self.settings = settings
        if settings and settings.host.rstrip("/") != self.host:
            raise ManifestError("Configured environment belongs to a different workspace.")

    @property
    def host(self) -> str:
        return self.workspace.config.host.rstrip("/")

    def check_host(self, project: dict[str, Any]) -> None:
        for binding in (project["runtime"], project["tracing"]):
            host = binding.get("workspace_host")
            if host and host.rstrip("/") != self.host:
                raise ManifestError("Project binding belongs to a different workspace; select its --profile.")

    def verify_binding(self, project: dict[str, Any], *, required: bool = False) -> Any:
        self.check_host(project)
        resource_id = project["runtime"]["resource_id"]
        if not resource_id:
            if required:
                raise ManifestError("No runtime resource is linked. Run aops link or aops deploy first.")
            return None
        if project["framework"] == "genie":
            return self.workspace.genie.get_space(space_id=resource_id)
        if project["framework"] in {"langgraph", "openai"}:
            return self.workspace.apps.get(name=resource_id)
        raise ManifestError("Skill agents execute locally through Codex, not a native remote binding.")

    def link(self, root: Path, resource_id: str, *, invocation_path: str | None = None) -> dict[str, Any]:
        manifest = validate_agent(root)
        if manifest.build_type == "skill":
            raise ManifestError("Skill agents do not require a remote runtime link.")
        if not resource_id.strip() or resource_id.startswith("-"):
            raise ManifestError("A resource ID is required.")
        project = load_project(root)
        self.check_host(project)
        project["runtime"].update(resource_id=resource_id, workspace_host=self.host)
        if invocation_path is not None:
            if not invocation_path.startswith("/") or invocation_path.startswith("//"):
                raise ManifestError("invocation_path must be an absolute path on the App.")
            project["runtime"]["invocation_path"] = invocation_path
        self.verify_binding(project, required=True)
        save_project(root, project)
        return project["runtime"]

    def deploy(
        self, root: Path, *, apply: bool, name: str | None = None,
        warehouse_id: str | None = None, parent_path: str | None = None,
    ) -> dict[str, Any]:
        manifest = validate_agent(root)
        if manifest.build_type == "skill":
            raise ManifestError("Skill agents run through aops run; native deployment is not supported.")
        project = load_project(root)
        self.check_host(project)
        if not apply:
            raise ManifestError("Deployment changes remote resources. Pass --apply to confirm.")
        # Reject unconfigured or conflicting tracing before any remote mutation.
        from .tracing import TraceStore

        tracing = resolve_project(root, self.settings)["tracing"]
        self.check_host({"runtime": project["runtime"], "tracing": tracing})
        store = TraceStore(self.profile, tracing)
        store.verify_location()
        if manifest.build_type == "code":
            deployment = name or manifest.agent_id
            if not re.fullmatch(r"[a-z][a-z0-9-]{0,48}", deployment):
                raise ManifestError("Deployment name must use lower-case letters, digits and hyphens.")
            app_name = f"agent-bricks-{deployment}"
            bound = project["runtime"]["resource_id"]
            if bound and bound != app_name:
                raise ManifestError("Deployment name differs from the linked App; use a separate project binding.")
            self.checked([
                "tracing", "bind", "--experiment-id", tracing["experiment_id"],
                "--source", str(root),
            ])
            deployed = self.checked(["deploy", deployment, "--source", str(root)])
            project["runtime"].update(resource_id=app_name, workspace_host=self.host)
            save_project(root, project)
            if (
                not isinstance(deployed, dict)
                or str(deployed.get("trace_experiment_id")) != tracing["experiment_id"]
                or deployed.get("trace_setup_error")
                or deployed.get("trace_grant_error")
                or deployed.get("trace_grant") != "granted"
                or any(deployed.get(key) for key in (
                    "store_grant_error", "model_service_grant_error", "prompt_grant_error",
                ))
            ):
                raise RuntimeError(
                    "App deployment was attempted and its binding was saved, but native MLflow "
                    "tracing/resource grants were not confirmed. Inspect the deployment before retrying."
                )
        else:
            definition = load_genie_definition(root)
            if not definition:
                raise ManifestError("genie.json is a draft. Supply an actual exported Genie definition.")
            warehouse = (
                warehouse_id or project["runtime"]["warehouse_id"]
                or (self.settings.warehouse_id if self.settings else "")
            )
            if not warehouse:
                raise ManifestError("Set a SQL warehouse with aops configure or override --warehouse-id.")
            payload = {
                "warehouse_id": warehouse, "serialized_space": json.dumps(definition),
                "title": manifest.name, "description": manifest.description,
            }
            parent = parent_path or project["runtime"]["parent_path"]
            if parent:
                payload["parent_path"] = parent
            if project["runtime"]["resource_id"]:
                current = self.verify_binding(project, required=True)
                etag = getattr(current, "etag", None)
                if not etag:
                    raise ManifestError("Genie update requires a server etag to prevent lost updates.")
                result = self.workspace.genie.update_space(
                    space_id=project["runtime"]["resource_id"], etag=etag, **payload,
                )
            else:
                result = self.workspace.genie.create_space(**payload)
            resource_id = getattr(result, "space_id", None)
            if not resource_id:
                raise RuntimeError("Genie deployment returned no space_id; inspect the workspace before retrying.")
            project["runtime"].update(
                resource_id=resource_id, workspace_host=self.host,
                parent_path=parent,
            )
            if warehouse_id:
                project["runtime"]["warehouse_id"] = warehouse_id
        save_project(root, project)
        return project["runtime"]

    def invoke(
        self, root: Path, *, payload: Any = None, prompt: str | None = None,
        conversation_id: str | None = None, routing_key: str | None = None,
    ) -> Any:
        project = load_project(root)
        self.verify_binding(project, required=True)
        resource_id = project["runtime"]["resource_id"]
        if project["framework"] == "genie":
            if not prompt or not prompt.strip():
                raise ManifestError("Genie run requires --prompt.")
            if conversation_id:
                response = self.workspace.genie.create_message_and_wait(
                    space_id=resource_id, conversation_id=conversation_id, content=prompt,
                )
            else:
                response = self.workspace.genie.start_conversation_and_wait(
                    space_id=resource_id, content=prompt,
                )
            status = getattr(response.status, "value", response.status)
            if status != "COMPLETED":
                raise RuntimeError(f"Genie conversation did not complete: {status}")
            return response.as_dict()
        path = project["runtime"]["invocation_path"]
        if not path or payload is None:
            raise ManifestError("Code run needs a linked invocation_path and --input JSON payload.")
        command = ["endpoint", "invoke", resource_id, "--path", path, "--json", json.dumps(payload)]
        if routing_key:
            command.extend(["--routing-key", routing_key])
        response = self.checked(command)
        status = response.get("status_code") if isinstance(response, dict) else None
        if not isinstance(status, int) or status < 200 or status >= 300:
            raise RuntimeError(f"Agent Bricks endpoint failed (HTTP {status}); inspect native diagnostics.")
        if status == 202:
            raise RuntimeError(
                "Invocation was accepted asynchronously, not completed. Use the native runtime's "
                "poll/replay protocol; no successful completion trace will be claimed."
            )
        return response

    def checked(self, arguments: list[str]) -> Any:
        result = self.runner(["agentbricks", "--profile", self.profile, "--output", "json", *arguments])
        if result.returncode:
            # Native error bodies can echo prompts, tokens, or connection strings.
            raise RuntimeError(f"Agent Bricks {arguments[0]} failed (exit {result.returncode}).")
        try:
            return json.loads(result.stdout)
        except (ValueError, TypeError):
            raise RuntimeError("Agent Bricks returned a non-JSON response; inspect the native CLI output.") from None

    @staticmethod
    def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, capture_output=True, text=True, check=False)

from __future__ import annotations

import io
import json
import subprocess
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from codex_agentops.cli import main
from codex_agentops.config import Settings
from codex_agentops.manifest import ManifestError, calculate_definition_checksums, find_agent_root, validate_agent
from codex_agentops.project import create_project, default_project, load_project, project_source_files, resolve_project, save_project
from codex_agentops.registry import AgentVersion, RegistryClient, build_agent_archive, extract_agent_archive, parse_registry
from codex_agentops.release import CodexRuntimeBuilder, ReleaseError, build_release, load_release, release_to_dict, write_release_metadata
from codex_agentops.runtime import NativeRuntime


HOST = "https://test.cloud.databricks.com"


def native_project(tmp_path, build_type="code"):
    def runner(command):
        root = Path(command[-1])
        root.mkdir()
        for name, body in {
            "agent.toml": '[agent]\nframework = "langgraph"\n',
            "pyproject.toml": '[project]\nname = "test-agent"\nversion = "1.0.0"\n',
            "AGENTS.md": "# Native Instructions\n", "README.md": "# Native README\n",
            ".env": "DATABRICKS_TOKEN=never-package-this\n",
        }.items():
            (root / name).write_text(body)
        (root / "agent").mkdir()
        (root / "agent/agent.py").write_text("def run():\n    return 'hello'\n")
        return subprocess.CompletedProcess(command, 0, "{}", "")

    return create_project(
        "test-agent", tmp_path / build_type, build_type=build_type,
        profile="synthetic-profile", runner=runner,
    )


@pytest.fixture
def code_project(tmp_path):
    return native_project(tmp_path)


@pytest.fixture
def genie_project(tmp_path):
    return native_project(tmp_path, "no-code")


def workspace(**kwargs):
    return SimpleNamespace(config=SimpleNamespace(host=HOST), **kwargs)


def test_native_init_requires_explicit_profile_and_uses_official_cli(tmp_path):
    with pytest.raises(ManifestError, match="--profile"):
        create_project("test-agent", tmp_path / "missing", build_type="code")
    calls = []

    def run(command):
        calls.append(command)
        Path(command[-1]).mkdir()
        return subprocess.CompletedProcess(command, 0, "{}", "")

    create_project("test-agent", tmp_path / "code", build_type="code", profile="chosen", runner=run)
    assert calls[0][:5] == ["agentbricks", "--profile", "chosen", "--output", "json"]
    assert "--disable-chat-app" in calls[0]
    assert "--server" in calls[0]


@pytest.mark.skipif(shutil.which("agentbricks") is None, reason="Official Agent Bricks CLI is not installed")
def test_installed_native_cli_scaffold_validates_without_contacting_a_workspace(tmp_path):
    # `init` only copies bundled templates and seeds .env with this nonexistent test profile.
    root = create_project(
        "local-test", tmp_path / "native", build_type="code", profile="aops-local-test-no-workspace",
    )
    assert validate_agent(root).build_type == "code"
    assert (root / "AGENTKIT_CONTRACT.md").is_file()
    assert (root / ".env").is_file()
    project = load_project(root)
    assert ".agentbricks/project.toml" in project["source_paths"]
    archive = project_source_files(root, project)
    assert root / ".agentbricks/project.toml" in archive
    assert root / ".env" not in archive
    assert not (root / "runtime/ui.py").exists()


@pytest.mark.parametrize("kind", ["code", "no-code"])
def test_common_release_roundtrip_and_source_hashes(tmp_path, kind):
    root = native_project(tmp_path, kind)
    manifest = validate_agent(root)
    assert manifest.build_type == kind
    assert find_agent_root(root) == root
    release = build_release(root, "1.0.0", "main.agentops")
    assert release.skills == ()
    assert release.harness_profile == ("agentbricks" if kind == "code" else "genie")
    assert release_to_dict(release)["agent_type"] == manifest.agent_type
    write_release_metadata(release)
    archive = build_agent_archive(root)
    assert "SPEC.md" in archive.files
    assert "aops.yaml" in archive.files
    assert ".env" not in archive.files
    destination = tmp_path / "restored"
    extract_agent_archive(archive.content, destination)
    assert validate_agent(destination).agent_type == manifest.agent_type
    assert load_release(destination).project == release.project
    assert calculate_definition_checksums(destination) == calculate_definition_checksums(root)
    assert build_agent_archive(root).content == archive.content
    with pytest.raises(ReleaseError, match="only Skill"):
        CodexRuntimeBuilder().prepare(root)


def test_native_snapshots_exclude_secrets_caches_and_unselected_sources(code_project):
    root = code_project
    for name in (".env.local", "key.pem", "tokens.json", "ignored.py"):
        (root / name).write_text("secret")
    (root / "agent/.env").write_text("secret")
    (root / "agent/__pycache__").mkdir()
    (root / "agent/__pycache__/module.pyc").write_bytes(b"cache")
    names = {path.relative_to(root).as_posix() for path in project_source_files(root, load_project(root))}
    assert "agent/agent.py" in names
    assert not any("secret" in (root / name).read_text() for name in names)
    assert "ignored.py" not in names


@pytest.mark.parametrize("unsafe", ["../outside", "/etc/passwd", ".", ".env", ".git", "key.pem"])
def test_explicit_unsafe_source_paths_are_rejected(code_project, unsafe):
    project = load_project(code_project)
    project["source_paths"].append(unsafe)
    save_project(code_project, project)
    with pytest.raises(ManifestError, match="Unsafe or excluded"):
        validate_agent(code_project)


def test_native_snapshot_rejects_symlink(code_project, tmp_path):
    secret = tmp_path / "secret.py"
    secret.write_text("secret")
    (code_project / "agent/link.py").symlink_to(secret)
    with pytest.raises(ManifestError, match="Symbolic links"):
        validate_agent(code_project)


def test_genie_draft_is_local_and_not_deployable(genie_project, monkeypatch):
    import codex_agentops.tracing as tracing

    monkeypatch.setattr(tracing, "TraceStore", lambda *args: SimpleNamespace(verify_location=lambda: None))
    project = load_project(genie_project)
    assert project["runtime"]["resource_id"] == ""
    assert validate_agent(genie_project).serving_type == "genie"
    runtime = NativeRuntime("chosen", workspace=workspace())
    with pytest.raises(ManifestError, match="--apply"):
        runtime.deploy(genie_project, apply=False)
    with pytest.raises(ManifestError, match="draft"):
        runtime.deploy(genie_project, apply=True, warehouse_id="warehouse")


def test_new_project_has_no_repeated_environment_fields(genie_project):
    import yaml

    raw = yaml.safe_load((genie_project / "aops.yaml").read_text())
    assert raw == {"schema_version": 1, "framework": "genie", "source_paths": ["genie.json"]}
    assert load_project(genie_project)["tracing"] == default_project("genie")["tracing"]


def test_partial_project_overrides_inherit_common_tracing_without_changing_source(genie_project):
    settings = Settings("chosen", HOST, warehouse_id="warehouse", registry="main.aops", experiment_id="123")
    project = load_project(genie_project)
    project["tracing"]["experiment_id"] = "456"
    save_project(genie_project, project)
    before = (genie_project / "aops.yaml").read_bytes()
    resolved = resolve_project(genie_project, settings)
    assert resolved["tracing"] == {**settings.tracing, "experiment_id": "456"}
    assert (genie_project / "aops.yaml").read_bytes() == before


def test_release_snapshots_inherited_environment_without_duplicating_project_config(genie_project):
    settings = Settings("chosen", HOST, warehouse_id="warehouse", registry="main.aops", experiment_id="123")
    before = (genie_project / "aops.yaml").read_bytes()
    release = build_release(genie_project, "1.0.0", "main.aops", settings=settings)
    assert release.project["tracing"] == settings.tracing
    write_release_metadata(release)
    assert load_release(genie_project).project["tracing"] == settings.tracing
    assert (genie_project / "aops.yaml").read_bytes() == before


@pytest.mark.parametrize("override,expected", [(None, "common-warehouse"), ("dedicated", "dedicated")])
def test_genie_deploy_inherits_common_warehouse_and_persists_only_explicit_override(genie_project, monkeypatch, override, expected):
    import yaml
    import codex_agentops.tracing as tracing

    settings = Settings("chosen", HOST, warehouse_id="common-warehouse", registry="main.aops", experiment_id="123")
    (genie_project / "genie.json").write_text('{"version":2}')
    calls = []
    monkeypatch.setattr(tracing, "TraceStore", lambda profile, binding: SimpleNamespace(
        verify_location=lambda: calls.append(binding),
    ))
    runtime = NativeRuntime("chosen", settings=settings, workspace=workspace(genie=SimpleNamespace(
        create_space=lambda **kwargs: calls.append(kwargs) or SimpleNamespace(space_id="space-1"),
    )))
    runtime.deploy(genie_project, apply=True, warehouse_id=override)
    assert calls[0] == settings.tracing
    assert calls[1]["warehouse_id"] == expected
    raw = yaml.safe_load((genie_project / "aops.yaml").read_text())
    assert "tracing" not in raw
    assert raw["runtime"].get("warehouse_id") == override


def test_code_deploy_checks_tracing_before_remote_mutation(code_project, monkeypatch):
    import codex_agentops.tracing as tracing

    def denied():
        raise ManifestError("wrong UC location")

    monkeypatch.setattr(tracing, "TraceStore", lambda *args: SimpleNamespace(verify_location=denied))
    runtime = NativeRuntime("chosen", workspace=workspace(), runner=lambda command: pytest.fail("must not deploy"))
    with pytest.raises(ManifestError, match="wrong UC location"):
        runtime.deploy(code_project, apply=True)


def test_code_deploy_uses_official_cli_and_does_not_register_or_promote(code_project, monkeypatch):
    import codex_agentops.tracing as tracing

    monkeypatch.setattr(tracing, "TraceStore", lambda *args: SimpleNamespace(verify_location=lambda: None))
    settings = Settings("chosen", HOST, experiment_id="1", registry="main.aops", warehouse_id="warehouse")
    commands = []

    def runner(command):
        commands.append(command)
        payload = {"trace_experiment_id": "1", "trace_grant": "granted"} if command[5] == "deploy" else {}
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    runtime = NativeRuntime("chosen", workspace=workspace(), runner=runner, settings=settings)
    result = runtime.deploy(code_project, apply=True, name="test-agent")
    assert result["resource_id"] == "agent-bricks-test-agent"
    assert commands[0][5:7] == ["tracing", "bind"]
    assert commands[0][commands[0].index("--experiment-id") + 1] == "1"
    assert commands[1][5:7] == ["deploy", "test-agent"]
    assert not (code_project / ".aops/release.json").exists()
    assert not load_project(code_project)["tracing"]["experiment_id"]


def test_genie_update_uses_server_etag_and_real_definition(genie_project, monkeypatch):
    import codex_agentops.tracing as tracing

    monkeypatch.setattr(tracing, "TraceStore", lambda *args: SimpleNamespace(verify_location=lambda: None))
    project = load_project(genie_project)
    project["runtime"].update(resource_id="space-1", workspace_host=HOST)
    save_project(genie_project, project)
    definition = {"version": 2, "data_sources": {"tables": [{"identifier": "main.data.actual"}]}}
    (genie_project / "genie.json").write_text(json.dumps(definition))
    calls = []
    runtime = NativeRuntime("chosen", workspace=workspace(genie=SimpleNamespace(
        get_space=lambda **kwargs: SimpleNamespace(etag="server-v1"),
        update_space=lambda **kwargs: calls.append(kwargs) or SimpleNamespace(space_id="space-1"),
    )))
    runtime.deploy(genie_project, apply=True, warehouse_id="warehouse")
    assert calls[0]["etag"] == "server-v1"
    assert json.loads(calls[0]["serialized_space"]) == definition


def test_codex_hook_ignores_native_projects(genie_project):
    from codex_agentops.hooks import handle_hook

    assert handle_hook({"cwd": str(genie_project), "hook_event_name": "SessionStart"}, launch_uploader=False) == {}


def test_link_checks_workspace_and_native_access(genie_project):
    calls = []
    runtime = NativeRuntime("chosen", workspace=workspace(
        genie=SimpleNamespace(get_space=lambda **kwargs: calls.append(kwargs) or object()),
    ))
    result = runtime.link(genie_project, "space-1")
    assert calls == [{"space_id": "space-1"}]
    assert result["workspace_host"] == HOST
    other = NativeRuntime("other", workspace=SimpleNamespace(config=SimpleNamespace(host="https://other.test")))
    with pytest.raises(ManifestError, match="different workspace"):
        other.verify_binding(load_project(genie_project))


def test_genie_run_uses_existing_conversation_without_reinventing_transport(genie_project):
    project = load_project(genie_project)
    project["runtime"].update(resource_id="space-1", workspace_host=HOST)
    save_project(genie_project, project)
    calls = []
    response = SimpleNamespace(status="COMPLETED", as_dict=lambda: {"conversation_id": "c", "attachments": []})
    runtime = NativeRuntime("chosen", workspace=workspace(genie=SimpleNamespace(
        get_space=lambda **kwargs: object(),
        create_message_and_wait=lambda **kwargs: calls.append(kwargs) or response,
    )))
    assert runtime.invoke(genie_project, prompt="question", conversation_id="c")["conversation_id"] == "c"
    assert calls[0] == {"space_id": "space-1", "conversation_id": "c", "content": "question"}


def test_code_invoke_preserves_opaque_payload_and_explicit_profile(code_project):
    project = load_project(code_project)
    project["runtime"].update(resource_id="app-1", workspace_host=HOST, invocation_path="/api/invocations")
    save_project(code_project, project)
    commands = []

    def runner(command):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, '{"status_code":200,"body":{"result":"ok"}}', "")

    runtime = NativeRuntime("chosen", workspace=workspace(apps=SimpleNamespace(get=lambda **kwargs: object())), runner=runner)
    payload = {"id": "native-id", "session_id": "s", "input": {"custom": [1, 2]}}
    result = runtime.invoke(code_project, payload=payload, routing_key="routing-only")
    assert result["body"] == {"result": "ok"}
    command = commands[0]
    assert command[:5] == ["agentbricks", "--profile", "chosen", "--output", "json"]
    assert json.loads(command[command.index("--json") + 1]) == payload
    assert command[-2:] == ["--routing-key", "routing-only"]


@pytest.mark.parametrize("status", [202, 401, 500])
def test_code_invoke_does_not_claim_async_or_http_failures_succeeded(code_project, status):
    project = load_project(code_project)
    project["runtime"].update(resource_id="app", workspace_host=HOST, invocation_path="/api/invocations")
    save_project(code_project, project)
    runtime = NativeRuntime(
        "chosen", workspace=workspace(apps=SimpleNamespace(get=lambda **kwargs: object())),
        runner=lambda command: subprocess.CompletedProcess(command, 0, json.dumps({"status_code": status}), ""),
    )
    with pytest.raises(RuntimeError):
        runtime.invoke(code_project, payload={})


@pytest.mark.parametrize("kind", ["code", "no-code"])
def test_registry_register_and_load_dispatch_native_types_without_uc_skill_calls(tmp_path, kind):
    root = native_project(tmp_path, kind)
    artifacts = {}

    class Files:
        def create_directory(self, path):
            pass

        def upload(self, path, contents, overwrite):
            assert overwrite is False
            artifacts[path] = contents.read()

        def download(self, path):
            return SimpleNamespace(contents=io.BytesIO(artifacts[path]))

    client = RegistryClient(
        profile="chosen", warehouse_id="warehouse", location=parse_registry("main.agentops"),
        workspace=workspace(files=Files()), skill_publisher=object(),
    )
    metadata = {}
    client.get = lambda *args, **kwargs: metadata.get("release")

    def register_metadata(**values):
        release = values["release"]
        metadata["release"] = AgentVersion(
            agent_id=release.agent_id, version=release.release_version, name="Agent",
            description="Agent", agent_type=release.agent_type, model=release.model,
            artifact_path=values["artifact_path"], artifact_sha256=values["artifact_sha256"],
            artifact_size=values["artifact_size"], harness_sha256=values["harness_sha256"],
            skills_sha256=values["skills_sha256"], skill_names=(), registered_by="publisher",
            registered_at="2026-10-21", release_manifest=values["release_manifest"],
        )

    client._register_metadata = register_metadata
    registered = client.register(root, "1.0.0")
    restored, fetched = client.load("test-agent", destination=tmp_path / "loaded")
    assert registered == fetched
    assert validate_agent(restored).build_type == kind
    assert not (restored / ".codex").exists()


def test_cli_genie_init_validate_does_not_prepare_codex(tmp_path, monkeypatch):
    import codex_agentops.cli as cli

    monkeypatch.setattr(cli, "_codex_runtime_builder", lambda: pytest.fail("Genie is not a Codex agent"))
    root = tmp_path / "genie"
    assert main(["init", "test-agent", "--type", "no-code", "--path", str(root)]) == 0
    assert main(["validate", str(root)]) == 0

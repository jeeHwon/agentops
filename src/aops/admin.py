"""`aops admin setup`: the organisation's shared pieces, shown first and applied with `--apply`.

Steps: registry tables, the active policy, the aops package for the Jobs, the aops server app, the
grants its service principal (and, if named, the users group) needs, and the Genie Code skill that
runs aops commands in the workspace. Run by a workspace admin with their own Databricks login.
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import tempfile
from importlib import resources
from pathlib import Path
from typing import Any

STEPS = ("tables", "policy", "dist", "app", "grants", "genie")
PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parents[1]  # the source checkout, when aops runs from one
# Server code lives under the workspace root, where only admins have rights (and they flow down).
# /Workspace/Shared would not do: it gives every user CAN_MANAGE, which a child folder cannot take away.
APP_ROOT = "/Workspace/aops/server"
APP_REQUIREMENTS = "fastapi>=0.162\nuvicorn>=0.54\npython-multipart>=0.0.20\ndatabricks-sdk>=0.158\n"
# Workspace skills (everyone's Genie Code reads them; only workspace admins write there).
GENIE_SKILLS = "/Workspace/.assistant/skills"
# The same aops code the CLI runs, as readable source: Genie Code imports it from here and can read
# what a command does before running it (an installed package is opaque to it).
GENIE_SOURCE = "/Workspace/aops/genie"
# No-code agent folders: the aops service principal makes one per agent and gives its author CAN MANAGE.
NO_CODE_ROOT = "/Workspace/aops/no-code"


def ddl() -> str:
    repo_file = REPO / "bundles" / "registry" / "tables.sql"
    if repo_file.is_file():
        return repo_file.read_text(encoding="utf-8")
    return resources.files("aops.dbx").joinpath("tables.sql").read_text(encoding="utf-8")


def statements(sql: str) -> list[str]:
    body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    return [part.strip().rstrip(";") for part in body.split(";\n") if part.strip().rstrip(";").strip()]


def add_command(sub: argparse._SubParsersAction) -> None:
    admin = sub.add_parser("admin", help="조직 공통 자원 준비 (관리자)")
    actions = admin.add_subparsers(dest="action", required=True)
    setup = actions.add_parser("setup", help="레지스트리·정책·Job용 패키지·aops 서버·권한 (미리보기, --apply로 실행)")
    setup.add_argument("--profile", required=True, help="관리자의 Databricks CLI 프로필")
    setup.add_argument("--registry", required=True, help="레지스트리 스키마 <catalog>.<schema>")
    setup.add_argument("--warehouse", required=True, help="SQL warehouse ID")
    setup.add_argument("--app", default="aops-server", help="aops 서버 앱 이름")
    setup.add_argument("--approval-mode", choices=["required", "optional", "auto"], default="required")
    setup.add_argument("--validation-mode", choices=["mandatory", "optional", "exempt"], default="mandatory")
    setup.add_argument("--approver-group", default="")
    setup.add_argument("--git-connection", help="Git 사본: GitHub API(https://api.github.com)로 가는 UC HTTP 연결 이름. "
                       "git 옵션이 없으면 지금 정책의 git 설정을 그대로 둔다")
    setup.add_argument("--git-owner", help="Git 사본: 에이전트 저장소를 둘 GitHub 조직")
    setup.add_argument("--git-repo-prefix", help="Git 사본: 저장소 이름 = <접두어><agent_id> (기본 aops-)")
    setup.add_argument("--no-git", action="store_true", help="Git 사본을 끈다 (저장소는 그대로 둔다)")
    setup.add_argument("--users-group", help="aops를 쓰는 사람들의 계정 그룹. 레지스트리 조회(USE·SELECT)와 "
                       "Genie Code가 쓰는 aops 코드 폴더 읽기(CAN_READ)를 준다")
    setup.add_argument("--genie-skills", default=GENIE_SKILLS,
                       help=f"Genie Code 스킬 폴더 (기본 {GENIE_SKILLS}: 워크스페이스 전체. 내 것만이면 /Users/<나>/.assistant/skills)")
    setup.add_argument("--only", help=f"쉼표로 고른 단계만 ({', '.join(STEPS)})")
    setup.add_argument("--apply", action="store_true", help="미리보기 대신 실행")


def cmd_admin(args: argparse.Namespace) -> int:
    from databricks.sdk import WorkspaceClient

    from .dbx.sql import Sql

    steps = [s.strip() for s in args.only.split(",")] if args.only else list(STEPS)
    w = WorkspaceClient(profile=args.profile)
    sql = Sql(w, args.warehouse, args.registry)
    catalog, schema = args.registry.split(".")
    me = w.current_user.me().user_name
    print(f"aops admin setup · {w.config.host} · {args.registry} · {'실행' if args.apply else '미리보기'} (관리자 {me})")

    if "tables" in steps:
        stmts = statements(ddl().replace("__SCHEMA__", args.registry))
        print(f"\n[tables] 레지스트리 테이블·뷰: 문 {len(stmts)}개 (IF NOT EXISTS, 다시 실행해도 됨)")
        if args.apply:
            for stmt in stmts:
                sql.run(stmt)
            print("  ok")

    current = _active_policy(sql) if ("policy" in steps or "grants" in steps) else None
    git = (json.loads(current["body"]) if current else {}).get("git")
    if "policy" in steps:
        body = policy_body(args, json.loads(current["body"]) if current else None)
        git = body.get("git")
        print(f"\n[policy] 정책: {json.dumps(body, ensure_ascii=False)}")
        if git and not (args.git_connection or args.no_git):
            print("  git: 지금 정책의 설정을 그대로 둡니다 (바꾸려면 --git-connection·--git-owner, 끄려면 --no-git)")
        if args.apply:
            if current and json.loads(current["body"]) == body:
                print(f"  같은 정책이 이미 적용돼 있습니다 (버전 {current['policy_version']})")
            else:
                version = (int(current["policy_version"]) if current else 0) + 1
                sql.run(f"UPDATE {sql.table('policies')} SET status = 'retired' WHERE status = 'active'")
                sql.run(f"INSERT INTO {sql.table('policies')} VALUES (:v, 'active', parse_json(:b), :by, current_timestamp())",
                        {"v": version, "b": body, "by": me})
                print(f"  정책 버전 {version} 적용")

    wheel_path = None
    if "dist" in steps or "app" in steps:
        print(f"\n[dist] Job용 aops 패키지: uv build → /Volumes/{catalog}/{schema}/aops_dist/")
        if args.apply:
            sql.run(f"CREATE VOLUME IF NOT EXISTS {args.registry}.aops_dist COMMENT 'aops validate·배포 Job이 설치하는 aops 패키지'")
            if not (REPO / "pyproject.toml").is_file():
                raise SystemExit("aops: dist 단계는 aops 저장소를 받은 폴더에서 실행합니다 (uv build). "
                                 "git clone 뒤 그 폴더에서 uv run --extra dbx aops admin setup ...")
            out = Path(tempfile.mkdtemp(prefix="aops-dist-"))
            subprocess.run(["uv", "build", "--wheel", "--out-dir", str(out)], cwd=REPO, check=True, capture_output=True)
            wheel = next(out.glob("aops_cli-*.whl"))
            wheel_path = f"/Volumes/{catalog}/{schema}/aops_dist/{wheel.name}"
            w.files.upload(wheel_path, io.BytesIO(wheel.read_bytes()), overwrite=True)
            print(f"  {wheel_path}")
        else:
            wheel_path = f"/Volumes/{catalog}/{schema}/aops_dist/aops_cli-<version>-py3-none-any.whl"

    app = None
    if "app" in steps:
        print(f"\n[app] aops 서버 앱 {args.app}: SQL warehouse {args.warehouse}(자원으로 붙이거나, 권한이 없으면 ID만), "
              f"사용자 권한 범위 {', '.join(USER_SCOPES)}, 코드 {APP_ROOT}/{args.app} (워크스페이스 관리자만 쓰기. 만들 권한이 없으면 내 홈 폴더)")
        if args.apply:
            app = _deploy_app(w, args, wheel_path, me)
            print(f"  {app.url}  (서비스 주체 {app.service_principal_client_id})")

    if "grants" in steps:
        if app is None:
            try:
                app = w.apps.get(args.app)
            except Exception:
                app = None
        sp = app.service_principal_client_id if app else "<앱 서비스 주체>"
        grants = [
            f"GRANT USE CATALOG ON CATALOG `{catalog}` TO `{sp}`",
            f"GRANT USE SCHEMA, CREATE VOLUME, SELECT, MODIFY ON SCHEMA `{catalog}`.`{schema}` TO `{sp}`",
            f"GRANT READ VOLUME ON VOLUME `{catalog}`.`{schema}`.`aops_dist` TO `{sp}`",
        ]
        if git:  # the Git mirror calls GitHub through this connection; only the aops service principal may use it
            grants.append(f"GRANT USE CONNECTION ON CONNECTION `{git['connection']}` TO `{sp}`")
        if args.users_group:  # people read the registry and install aops in a notebook; they write nothing
            group = args.users_group
            grants += [
                f"GRANT USE CATALOG ON CATALOG `{catalog}` TO `{group}`",
                f"GRANT USE SCHEMA, SELECT ON SCHEMA `{catalog}`.`{schema}` TO `{group}`",
            ]
        print("\n[grants] aops 서버 서비스 주체 권한 (사람에게는 USE·SELECT만 준다)")
        for grant in grants:
            print(f"  {grant}")
        print(f"  워크스페이스 폴더 {NO_CODE_ROOT}: {sp} CAN_MANAGE (노코드 에이전트 폴더를 만들고 작성자에게 CAN MANAGE를 줌)")
        print("  노코드 에이전트가 쓰는 테이블: 운영용 Genie를 만드는 서비스 주체에 SELECT가 필요 (데이터 주인이 줌)")
        if args.apply:
            failed = []
            for grant in grants:
                try:
                    sql.run(grant)
                except Exception as error:  # e.g. no MANAGE on the catalog in a shared workspace
                    failed.append((grant, str(error).splitlines()[0][:160]))
            if not failed:
                print("  ok")
            else:
                print("  aops: 아래 권한은 지금 계정으로 줄 수 없습니다. 그 카탈로그·스키마의 주인에게 부탁하세요:")
                for grant, why in failed:
                    print(f"    {grant};   -- {why}")
            if app is not None:
                _put_no_code_root(w, sp)

    if "genie" in steps:
        target = f"{args.genie_skills.rstrip('/')}/aops/SKILL.md"
        print(f"\n[genie] Genie Code 스킬 {target}: \"이 에이전트 등록해줘\" → 노트북에서 aops register와 같은 명령. "
              f"aops 코드는 {GENIE_SOURCE}/src (읽기만, 관리자만 쓰기)")
        if args.apply:
            source = _put_genie_source(w, args, me)
            _put_genie_skill(w, target, genie_skill(source, args.app))

    if not args.apply:
        print("\n미리보기입니다. 실행하려면 --apply를 붙이세요.")
    return 0


def genie_skill(source: str, app: str) -> str:
    """The workspace skill: import this aops from its source folder, then run the same CLI commands."""
    text = resources.files("aops.dbx").joinpath("genie_skill.md").read_text(encoding="utf-8")
    return text.replace("__SRC__", source).replace("__APP__", app)


def _put_genie_source(w: Any, args: argparse.Namespace, me: str) -> str:
    """Copy this aops package (without the server) to the Genie source folder; the folder for sys.path."""
    root = GENIE_SOURCE
    try:
        w.workspace.mkdirs(f"{root}/src")
    except Exception as error:
        if "permission" not in str(error).lower():
            raise
        root = f"/Workspace/Users/{me}/aops/genie"
        print(f"  aops: {GENIE_SOURCE}를 만들 권한이 없어 내 홈 폴더({root})에 둡니다")
        w.workspace.mkdirs(f"{root}/src")
    build = Path(tempfile.mkdtemp(prefix="aops-genie-"))
    shutil.copytree(PACKAGE, build / "aops", ignore=shutil.ignore_patterns("__pycache__", "server"))
    subprocess.run(["databricks", "workspace", "import-dir", str(build), f"{root}/src", "--overwrite", "-p", args.profile],
                   check=True, capture_output=True)
    print(f"  aops 코드: {root}/src/aops")
    if args.users_group:
        from databricks.sdk.service.workspace import WorkspaceObjectAccessControlRequest, WorkspaceObjectPermissionLevel

        status = w.workspace.get_status(root)
        try:
            w.workspace.update_permissions("directories", str(status.object_id), access_control_list=[
                WorkspaceObjectAccessControlRequest(group_name=args.users_group, permission_level=WorkspaceObjectPermissionLevel.CAN_READ)])
            print(f"  {args.users_group}: CAN_READ")
        except Exception as error:
            print(f"  aops: {args.users_group}에 읽기 권한을 주지 못했습니다 ({str(error).splitlines()[0][:120]})")
    return f"{root}/src"


def _put_no_code_root(w: Any, sp: str) -> None:
    """The folder that holds no-code agent folders; the aops service principal manages what is inside."""
    from databricks.sdk.service.workspace import WorkspaceObjectAccessControlRequest, WorkspaceObjectPermissionLevel

    try:
        w.workspace.mkdirs(NO_CODE_ROOT)
        status = w.workspace.get_status(NO_CODE_ROOT)
        w.workspace.update_permissions("directories", str(status.object_id), access_control_list=[
            WorkspaceObjectAccessControlRequest(service_principal_name=sp, permission_level=WorkspaceObjectPermissionLevel.CAN_MANAGE)])
        print(f"  {NO_CODE_ROOT}: 서비스 주체 CAN_MANAGE")
    except Exception as error:
        print(f"  aops: {NO_CODE_ROOT}를 준비하지 못했습니다 ({str(error).splitlines()[0][:120]}). "
              "서버는 서비스 주체의 홈 폴더(/Workspace/Users/<서비스 주체>/aops/no-code)를 대신 씁니다")


def _put_genie_skill(w: Any, target: str, text: str) -> None:
    from databricks.sdk.service.workspace import ImportFormat

    try:
        w.workspace.mkdirs(target.rsplit("/", 1)[0])
        w.workspace.upload(target, io.BytesIO(text.encode("utf-8")), format=ImportFormat.AUTO, overwrite=True)
        print("  ok (새 Genie Code 대화부터 읽힘)")
    except Exception as error:
        print(f"  aops: 스킬을 올리지 못했습니다 ({str(error).splitlines()[0][:160]}). "
              "워크스페이스 스킬 폴더는 워크스페이스 관리자만 씁니다. 내 것만 쓰려면 --genie-skills /Users/<나>/.assistant/skills")


def policy_body(args: argparse.Namespace, current: dict[str, Any] | None) -> dict[str, Any]:
    """The policy to apply. Git settings carry over from the active policy unless git options are given."""
    body: dict[str, Any] = {
        "validation_mode": args.validation_mode, "approval_mode": args.approval_mode, "approval_channel": "console",
        "approver_group": args.approver_group, "required_approvals": 1, "security_levels": ["general", "important", "secret"]}
    git = (current or {}).get("git")
    if getattr(args, "no_git", False):
        git = None
    elif getattr(args, "git_connection", None) or getattr(args, "git_owner", None):
        if not (args.git_connection and args.git_owner):
            raise SystemExit("aops: --git-connection과 --git-owner를 함께 적습니다")
        # mode commit: each version goes straight onto main. A Git failure never blocks a deploy.
        git = {"provider": "github", "connection": args.git_connection, "owner": args.git_owner,
               "repo_prefix": args.git_repo_prefix or "aops-", "mode": "commit"}
    if git:
        body["git"] = git
    return body


def _active_policy(sql: Any) -> dict[str, Any] | None:
    try:
        return sql.one(f"SELECT policy_version, body FROM {sql.table('policies')} WHERE status = 'active' "
                       "ORDER BY policy_version DESC LIMIT 1")
    except Exception:  # no registry tables yet (first setup)
        return None


def _deploy_app(w: Any, args: argparse.Namespace, wheel_path: str | None, me: str) -> Any:
    from databricks.sdk.service.apps import App, AppDeployment, AppResource, AppResourceSqlWarehouse, \
        AppResourceSqlWarehouseSqlWarehousePermission

    try:
        app = w.apps.get(args.app)
    except Exception:
        resource = AppResource(name="sql-warehouse", sql_warehouse=AppResourceSqlWarehouse(
            id=args.warehouse, permission=AppResourceSqlWarehouseSqlWarehousePermission.CAN_USE))
        description = "aops 서버: console 화면과 /api/v1"
        try:
            app = w.apps.create_and_wait(App(name=args.app, description=description, resources=[resource], user_api_scopes=list(USER_SCOPES)))
        except Exception as error:
            if "permission" not in str(error).lower():
                raise
            # Adding a warehouse as an app resource needs Can manage on it. Without that the app gets the
            # warehouse ID only and uses it through its own Can use (for example the users group's).
            print(f"  aops: warehouse를 앱 자원으로 붙일 권한이 없어 ID만 넘깁니다 ({str(error).splitlines()[0][:120]})")
            app = w.apps.create_and_wait(App(name=args.app, description=description, user_api_scopes=list(USER_SCOPES)))
    _ensure_user_scopes(w, app)
    has_resource = any(r.name == "sql-warehouse" for r in (app.resources or []))
    build = Path(tempfile.mkdtemp(prefix="aops-app-"))
    shutil.copytree(PACKAGE, build / "src" / "aops", ignore=shutil.ignore_patterns("__pycache__"))
    (build / "requirements.txt").write_text(APP_REQUIREMENTS)
    env = [{"name": "PYTHONPATH", "value": "src"}, {"name": "AOPS_REGISTRY", "value": args.registry},
           {"name": "AOPS_WAREHOUSE_ID", **({"valueFrom": "sql-warehouse"} if has_resource else {"value": args.warehouse})},
           {"name": "AOPS_DIST_WHEEL", "value": wheel_path or ""}]
    command = ["python", "-m", "uvicorn", "aops.server.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
    lines = ["command: " + json.dumps(command), "env:"]
    for item in env:
        key = "valueFrom" if "valueFrom" in item else "value"
        lines.append(f"  - name: {item['name']}\n    {key}: {json.dumps(item[key])}")
    (build / "app.yaml").write_text("\n".join(lines) + "\n")
    target = f"{APP_ROOT}/{args.app}"
    try:
        w.workspace.mkdirs(target)
    except Exception as error:
        if "permission" not in str(error).lower():
            raise
        # No right to make a folder under the workspace root: a home folder is also writable only by its
        # owner and workspace admins (it just has to move if the admin changes).
        target = f"/Workspace/Users/{me}/aops/server/{args.app}"
        print(f"  aops: {APP_ROOT}를 만들 권한이 없어 내 홈 폴더({target})에 둡니다. 본인과 워크스페이스 관리자만 고칠 수 있습니다")
        w.workspace.mkdirs(target)
    subprocess.run(["databricks", "workspace", "import-dir", str(build), target, "--overwrite", "-p", args.profile],
                   check=True, capture_output=True)
    w.apps.deploy_and_wait(args.app, AppDeployment(source_code_path=target))
    _report_code_folder(w, target)
    old = f"/Workspace/Users/{me}/{args.app}"
    try:
        if old != target:
            w.workspace.get_status(old)
            print(f"  옛 코드 위치 {old}는 더 쓰지 않습니다 (지워도 됩니다)")
    except Exception:
        pass
    return w.apps.get(args.app)


# What the server does with the requester's own token (Databricks Apps user authorization):
# - files: read the requester's UC skill (load)
# - genie: make, copy or move the dev Genie space of a no-code agent with the author's rights
# - workspace.workspace: write a new agent's template into the requester's workspace folder
# Who the requester is and their groups (so the console in a browser knows owners, approvers and admins like
# the CLI does) come with the default scopes every app gets (iam.current-user:read, iam.access-control:read);
# they cannot be set and are not listed here.
USER_SCOPES = ("files", "genie", "workspace.workspace")
IDENTITY_SCOPES = ("iam.current-user:read",)


def _ensure_user_scopes(w: Any, app: Any) -> None:
    current = set(app.user_api_scopes or [])
    effective = current | set(app.effective_user_api_scopes or [])
    missing = [s for s in IDENTITY_SCOPES if s not in effective]
    if missing:
        print(f"  aops: 앱에 {', '.join(missing)} 범위가 없어 화면이 그룹(담당 팀·승인 그룹·관리자)을 모릅니다")
    wanted = [s for s in USER_SCOPES if s not in current]
    if not wanted:
        return
    try:
        app.user_api_scopes = sorted(current | set(wanted))
        w.apps.update(app.name, app)
        print(f"  사용자 권한 범위: {', '.join(app.user_api_scopes)}")
        return
    except Exception as error:
        first = str(error).splitlines()[0][:120]
    added, failed = [], []
    for scope in wanted:  # one at a time: a scope this workspace does not know must not block the others
        try:
            app.user_api_scopes = sorted(current | {scope})
            w.apps.update(app.name, app)
            current.add(scope)
            added.append(scope)
        except Exception as error:
            failed.append(f"{scope} ({str(error).splitlines()[0][:80]})")
    app.user_api_scopes = sorted(current)
    if added:
        print(f"  사용자 권한 범위: {', '.join(sorted(current))}")
    if failed:
        print(f"  aops: 앱 사용자 권한 범위를 더하지 못했습니다: {'; '.join(failed) or first}. "
              "앱 설정 → User authorization에서 더하세요")


def _report_code_folder(w: Any, path: str) -> None:
    """Who can change the server code: should be workspace admins only."""
    status = w.workspace.get_status(path)
    acl = w.workspace.get_permissions("directories", str(status.object_id)).access_control_list or []
    writers = sorted({a.group_name or a.user_name or a.service_principal_name for a in acl
                      for p in (a.all_permissions or []) if p.permission_level and p.permission_level.value in ("CAN_EDIT", "CAN_MANAGE")})
    print(f"  코드 폴더 쓰기 권한: {', '.join(writers) or '없음'}")
    if any(name in ("users", "account users") for name in writers):
        print("  aops: 모든 사용자가 서버 코드를 고칠 수 있습니다. 폴더 권한을 확인하세요")

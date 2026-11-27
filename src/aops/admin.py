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


def run_ddl(sql: Any, stmts: list[str]) -> None:
    """Run the registry DDL. A column added later (`ALTER TABLE … ADD COLUMNS`) is skipped when it is there already."""
    for stmt in stmts:
        try:
            sql.run(stmt)
        except Exception as error:
            if "ADD COLUMNS" in stmt.upper() and "FIELD_ALREADY_EXISTS" in str(error):
                continue
            raise


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
    setup.add_argument("--service-principal", help="서버를 앱이 아니라 컨테이너로 띄울 때: 서버가 쓰는 서비스 주체의 application ID. "
                                                   "app 단계는 건너뛰고 grants가 이 주체에 권한(warehouse 사용 포함)을 준다 (docs/container.md)")
    setup.add_argument("--server-url", help="앱이 아닌 서버의 주소 (https://…). Genie Code 스킬이 이 주소를 부른다")
    setup.add_argument("--approval-mode", choices=["required", "optional", "auto"], default="required")
    setup.add_argument("--validation-mode", choices=["mandatory", "optional", "exempt"], default="mandatory")
    setup.add_argument("--approver-group", default="")
    setup.add_argument("--dev-approver", action="append", default=None,
                       help="개발용 예외 승인자(본인 요청도 승인, 승인마다 예외로 기록). 여러 번. 주지 않으면 지금 정책 값을 그대로 둔다")
    setup.add_argument("--git-connection", help="Git 사본: GitHub API(https://api.github.com)로 가는 UC HTTP 연결 이름. "
                       "git 옵션이 없으면 지금 정책의 git 설정을 그대로 둔다")
    setup.add_argument("--git-owner", help="Git 사본: 에이전트 저장소를 둘 GitHub 조직")
    setup.add_argument("--git-repo-prefix", help="Git 사본: 저장소 이름 = <접두어><agent_id> (기본 aops-)")
    setup.add_argument("--no-git", action="store_true", help="Git 사본을 끈다 (저장소는 그대로 둔다)")
    setup.add_argument("--workspace", action="append", default=None, metavar="NAME=URL;roles=dev,target[;catalogs=a,b][;warehouse=ID]",
                       help="여러 워크스페이스 (docs/workspaces.md): 개발(dev)·운영(target) 워크스페이스를 정책에 더하거나 바꾼다. 여러 번. "
                            "catalogs: 운영 데이터가 있어도 되는 카탈로그, warehouse: 그 워크스페이스의 warehouse(운영용 Genie, 개발용 Genie 기본). "
                            "주지 않으면 지금 정책 값을 그대로 둔다")
    setup.add_argument("--default-target", help="기본 운영 워크스페이스 (SPEC.md target_workspace가 없을 때)")
    setup.add_argument("--workspace-secret-scope", help="다른 워크스페이스를 부를 aops 서비스 주체의 OAuth client ID·secret이 든 시크릿 범위 "
                                                       "(키 client-id · client-secret). 서버와 평가·배포 Job이 읽는다")
    setup.add_argument("--no-workspaces", action="store_true", help="워크스페이스 목록을 지운다 (다시 서버의 워크스페이스 하나)")
    setup.add_argument("--for-workspace", metavar="NAME",
                       help="정책의 다른 워크스페이스(개발·운영)를 그 워크스페이스 관리자 프로필로 준비: --service-principal의 "
                            "no-code 폴더 권한과 warehouse CAN_USE, (--server-url이 있으면) Genie Code 스킬. 레지스트리·서버 단계는 하지 않는다")
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
    container = getattr(args, "service_principal", None)  # a server outside Databricks Apps (docs/container.md)
    if getattr(args, "for_workspace", None):
        return setup_other_workspace(args)
    if container and "app" in steps:
        steps.remove("app")
        print("aops: --service-principal: 서버는 컨테이너로 띄우니 app 단계는 건너뜁니다")
    w = WorkspaceClient(profile=args.profile)
    sql = Sql(w, args.warehouse, args.registry)
    catalog, schema = args.registry.split(".")
    me = w.current_user.me().user_name
    print(f"aops admin setup · {w.config.host} · {args.registry} · {'실행' if args.apply else '미리보기'} (관리자 {me})")

    if "tables" in steps:
        stmts = statements(ddl().replace("__SCHEMA__", args.registry))
        print(f"\n[tables] 레지스트리 테이블·뷰: 문 {len(stmts)}개 (IF NOT EXISTS, 다시 실행해도 됨)")
        if args.apply:
            run_ddl(sql, stmts)
            print("  ok")

    current = _active_policy(sql) if ("policy" in steps or "grants" in steps) else None
    git = (json.loads(current["body"]) if current else {}).get("git")
    scope = ((json.loads(current["body"]) if current else {}).get("workspace_credentials") or {}).get("secret_scope")
    if "policy" in steps:
        body = policy_body(args, json.loads(current["body"]) if current else None)
        git = body.get("git")
        scope = (body.get("workspace_credentials") or {}).get("secret_scope")
        for name, entry in (body.get("workspaces") or {}).items():
            print(f"  워크스페이스 {name}: {entry['host']} ({', '.join(entry.get('roles') or [])})"
                  + (f", 운영 데이터 카탈로그 {', '.join(entry['catalogs'])}" if entry.get("catalogs") else ""))
        if body.get("workspaces"):
            print("  그 워크스페이스마다 관리자 프로필로: aops admin setup --for-workspace <이름> --service-principal <aops 서비스 주체> ... --apply")
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
        if app is None and not container:
            try:
                app = w.apps.get(args.app)
            except Exception:
                app = None
        sp = container or (app.service_principal_client_id if app else "<앱 서비스 주체>")
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
        if container:  # an app gets the warehouse as its resource; a container's service principal is given it here
            print(f"  SQL warehouse {args.warehouse}: {sp} CAN_USE")
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
            if container:
                _grant_warehouse(w, args.warehouse, sp)
            if app is not None or container:
                _put_no_code_root(w, sp)
            if scope:
                _grant_scope(w, scope, sp)

    if "genie" in steps:
        target = f"{args.genie_skills.rstrip('/')}/aops/SKILL.md"
        print(f"\n[genie] Genie Code 스킬 {target}: \"이 에이전트 등록해줘\" → 노트북에서 aops register와 같은 명령. "
              f"aops 코드는 {GENIE_SOURCE}/src (읽기만, 관리자만 쓰기)")
        if args.apply:
            source = _put_genie_source(w, args, me)
            _put_genie_skill(w, target, genie_skill(source, args.app, getattr(args, "server_url", None)))

    if container:
        print("\n[container] 서버 컨테이너의 환경 변수 (docs/container.md). 비밀값은 플랫폼 시크릿으로 넣는다")
        for line in container_env(w.config.host, args, wheel_path if ("dist" in steps) else None):
            print(f"  {line}")

    if not args.apply:
        print("\n미리보기입니다. 실행하려면 --apply를 붙이세요.")
    return 0


def container_env(host: str, args: argparse.Namespace, wheel_path: str | None) -> list[str]:
    """What a container server needs: the workspace, its own service principal, the registry, the login."""
    catalog, schema = args.registry.split(".")
    wheel = wheel_path or f"/Volumes/{catalog}/{schema}/aops_dist/aops_cli-<version>-py3-none-any.whl"
    return [f"DATABRICKS_HOST={host}", f"DATABRICKS_CLIENT_ID={args.service_principal}",
            "DATABRICKS_CLIENT_SECRET=<서비스 주체의 OAuth secret>  # 시크릿",
            f"AOPS_REGISTRY={args.registry}", f"AOPS_WAREHOUSE_ID={args.warehouse}", f"AOPS_DIST_WHEEL={wheel}",
            "AOPS_OAUTH_CLIENT_ID=<계정 콘솔 App connections의 client ID>",
            f"AOPS_PUBLIC_URL={getattr(args, 'server_url', None) or 'https://<서버 주소>'}",
            "AOPS_SESSION_KEY=<Fernet 키>  # 시크릿"]


def genie_skill(source: str, app: str, server_url: str | None = None) -> str:
    """The workspace skill: import this aops from its source folder, then run the same CLI commands.
    A server outside Databricks Apps is called at its address (`--server-url`) with the notebook's own token."""
    text = resources.files("aops.dbx").joinpath("genie_skill.md").read_text(encoding="utf-8")
    text = text.replace("__SRC__", source)
    if server_url:
        text = text.replace('os.environ["AOPS_APP"] = "__APP__"', f'os.environ["AOPS_SERVER"] = "{server_url.rstrip("/")}"')
    return text.replace("__APP__", app)


def _grant_warehouse(w: Any, warehouse: str, sp: str) -> None:
    from databricks.sdk.service.sql import WarehouseAccessControlRequest, WarehousePermissionLevel

    try:
        w.warehouses.update_permissions(warehouse, access_control_list=[
            WarehouseAccessControlRequest(service_principal_name=sp, permission_level=WarehousePermissionLevel.CAN_USE)])
        print(f"  warehouse {warehouse}: {sp} CAN_USE")
    except Exception as error:
        print(f"  aops: warehouse 권한을 주지 못했습니다 ({str(error).splitlines()[0][:160]}). warehouse 주인에게 CAN_USE를 부탁하세요")


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


def _grant_scope(w: Any, scope: str, sp: str) -> None:
    """The server and its Jobs read the cross-workspace credentials from this scope as the server's service principal."""
    from databricks.sdk.service.workspace import AclPermission

    try:
        w.secrets.put_acl(scope, sp, AclPermission.READ)
        print(f"  시크릿 범위 {scope}: {sp} READ (키 client-id · client-secret: 다른 워크스페이스를 부를 서비스 주체)")
    except Exception as error:
        print(f"  aops: 시크릿 범위 {scope}에 READ를 주지 못했습니다 ({str(error).splitlines()[0][:120]}). "
              f"범위를 만들고 두 키를 넣은 뒤 다시: databricks secrets create-scope {scope}; "
              f"databricks secrets put-secret {scope} client-id; databricks secrets put-secret {scope} client-secret")


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
    for key in ("approvers_required", "dev_approvers", "review", "evaluate", "no_code"):  # set on the console: kept
        if key in (current or {}):
            body[key] = current[key]
    if getattr(args, "dev_approver", None) is not None:
        body["dev_approvers"] = [a.strip() for a in args.dev_approver if a.strip()]
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
    body.update(workspace_settings(args, current))
    return body


def parse_workspace(text: str) -> tuple[str, dict[str, Any]]:
    """`NAME=URL;roles=dev,target;catalogs=a,b;warehouse=ID` → (name, {host, roles, catalogs, warehouse_id})."""
    head, *rest = [part.strip() for part in text.split(";") if part.strip()]
    name, _, url = head.partition("=")
    entry: dict[str, Any] = {"host": url.strip(), "roles": ["dev", "target"]}
    for part in rest:
        key, _, value = part.partition("=")
        values = [v.strip() for v in value.split(",") if v.strip()]
        if key.strip() == "roles":
            entry["roles"] = values
        elif key.strip() == "catalogs":
            entry["catalogs"] = values
        elif key.strip() in ("warehouse", "warehouse_id"):
            entry["warehouse_id"] = value.strip()
        else:
            raise SystemExit(f"aops: --workspace의 {key!r}를 모릅니다 (roles · catalogs · warehouse)")
    return name.strip(), entry


def workspace_settings(args: argparse.Namespace, current: dict[str, Any] | None) -> dict[str, Any]:
    """workspaces · default_target · workspace_credentials: kept from the active policy unless given."""
    from . import workspaces

    out: dict[str, Any] = {k: current[k] for k in ("workspaces", "default_target", "workspace_credentials") if k in (current or {})}
    if getattr(args, "no_workspaces", False):
        return {}
    listed = dict(out.get("workspaces") or {})
    for text in getattr(args, "workspace", None) or []:
        name, entry = parse_workspace(text)
        try:
            entry["host"] = workspaces.normalize_host(entry["host"])
        except ValueError as error:
            raise SystemExit(f"aops: --workspace {name}: {error}") from None
        listed[name] = entry
    problems = workspaces.check_entries(listed)
    if problems:
        raise SystemExit("aops: --workspace: " + "; ".join(problems))
    if listed:
        out["workspaces"] = listed
    if getattr(args, "default_target", None):
        out["default_target"] = args.default_target
    if out.get("default_target"):
        roles = (listed.get(out["default_target"]) or {}).get("roles") or []
        if "target" not in roles:
            raise SystemExit(f"aops: --default-target {out['default_target']}는 roles에 target이 있는 --workspace여야 합니다")
    if getattr(args, "workspace_secret_scope", None):
        out["workspace_credentials"] = {"secret_scope": args.workspace_secret_scope, "client_id_key": "client-id",
                                        "client_secret_key": "client-secret"}
    return out


def setup_other_workspace(args: argparse.Namespace) -> int:
    """Another workspace of the policy, with an admin profile of that workspace: the pieces aops needs there.

    The registry, the server and the Jobs stay in the server's workspace. Here: the aops service principal is
    assigned (an account admin assigns it), it manages /Workspace/aops/no-code (dev folders and production Genie
    spaces), it may use the warehouse, and Genie Code gets the aops skill (only for a server outside Apps: a
    notebook here cannot get a token for an app in another workspace)."""
    from databricks.sdk import WorkspaceClient

    sp = getattr(args, "service_principal", None)
    if not sp:
        raise SystemExit("aops: --for-workspace에는 그 워크스페이스에서 일할 aops 서비스 주체 --service-principal <application ID>가 필요합니다")
    w = WorkspaceClient(profile=args.profile)
    me = w.current_user.me().user_name
    print(f"aops admin setup --for-workspace {args.for_workspace} · {w.config.host} · {'실행' if args.apply else '미리보기'} (관리자 {me})")
    found = list(w.service_principals.list(filter=f'applicationId eq "{sp}"', attributes="applicationId,displayName"))
    print(f"\n[assign] aops 서비스 주체 {sp}: {'이 워크스페이스에 있음' if found else '이 워크스페이스에 없음'}")
    if not found:
        print("  계정 관리자가 계정 콘솔에서 이 서비스 주체를 이 워크스페이스에 배정해야 합니다 (Workspaces → Permissions)")
        return 1
    print(f"\n[grants] {NO_CODE_ROOT}: {sp} CAN_MANAGE · SQL warehouse {args.warehouse}: {sp} CAN_USE")
    print("  노코드 데이터 테이블: 서비스 주체에 SELECT (데이터 주인이 줌)")
    if args.apply:
        _put_no_code_root(w, sp)
        _grant_warehouse(w, args.warehouse, sp)
    if getattr(args, "server_url", None):
        target = f"{args.genie_skills.rstrip('/')}/aops/SKILL.md"
        print(f"\n[genie] Genie Code 스킬 {target} → aops 서버 {args.server_url} (노트북 토큰과 이 워크스페이스 주소를 보냄)")
        if args.apply:
            source = _put_genie_source(w, args, me)
            _put_genie_skill(w, target, genie_skill(source, args.app, args.server_url))
    else:
        print("\n[genie] 건너뜀: 다른 워크스페이스의 노트북은 aops 앱의 토큰을 받을 수 없습니다. 앱 밖 서버면 --server-url을 주세요 (docs/workspaces.md)")
    if not args.apply:
        print("\n미리보기입니다. 실행하려면 --apply를 붙이세요.")
    return 0


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
# What the server does with the requester's own token: workspace folders (skill·code agents), Genie spaces (no-code),
# and the lists of the new-agent screen (Unity Catalog tables they may SELECT, SQL warehouses), read-only.
USER_SCOPES = ("files", "genie", "workspace.workspace", "catalog.catalogs:read", "catalog.schemas:read", "catalog.tables:read",
               "sql:read")
IDENTITY_SCOPES = ("iam.current-user:read",)


def _ensure_user_scopes(w: Any, app: Any) -> None:
    """Add the user scopes the server needs. Checked by reading the app again: the plain app update
    does not change scopes (only an app update with update_mask user_api_scopes does)."""
    current = set(app.user_api_scopes or [])
    effective = current | set(app.effective_user_api_scopes or [])
    missing = [s for s in IDENTITY_SCOPES if s not in effective]
    if missing:
        print(f"  aops: 앱에 {', '.join(missing)} 범위가 없어 화면이 그룹(담당 팀·승인 그룹·관리자)을 모릅니다")
    wanted = [s for s in USER_SCOPES if s not in current]
    if not wanted:
        return
    errors: dict[str, str] = {}
    try:
        current = _set_user_scopes(w, app.name, current | set(wanted))
    except Exception as error:
        first = str(error).splitlines()[0][:120]
        for scope in wanted:  # one at a time: a scope this workspace does not allow must not block the others
            try:
                current = _set_user_scopes(w, app.name, current | {scope})
            except Exception as failure:
                errors[scope] = str(failure).splitlines()[0][:100] or first
    app.user_api_scopes = sorted(current)
    added = [s for s in wanted if s in current]
    failed = [f"{s} ({errors.get(s, '앱 설정에 반영되지 않음')})" for s in wanted if s not in current]
    if added:
        print(f"  사용자 권한 범위: {', '.join(sorted(current))} (더함: {', '.join(added)})")
        print("  범위를 더하기 전에 열어 둔 화면은 예전 토큰을 씁니다: 시크릿 창으로 열거나 앱 주소의 쿠키를 지운 뒤 다시 열어 동의하세요")
    if failed:
        print(f"  aops: 앱 사용자 권한 범위를 더하지 못했습니다: {'; '.join(failed)}. 앱 설정 → User authorization에서 더하거나, "
              "워크스페이스 설정 → Development → Apps의 허용 범위를 확인하세요")


def _set_user_scopes(w: Any, name: str, scopes: set[str]) -> set[str]:
    """An app update of user_api_scopes only, then the scopes the app really has."""
    from datetime import timedelta

    from databricks.sdk.service.apps import App

    w.apps.create_update_and_wait(name, "user_api_scopes", app=App(name=name, user_api_scopes=sorted(scopes)),
                                  timeout=timedelta(minutes=10))
    return set(w.apps.get(name).user_api_scopes or [])


def _report_code_folder(w: Any, path: str) -> None:
    """Who can change the server code: should be workspace admins only."""
    status = w.workspace.get_status(path)
    acl = w.workspace.get_permissions("directories", str(status.object_id)).access_control_list or []
    writers = sorted({a.group_name or a.user_name or a.service_principal_name for a in acl
                      for p in (a.all_permissions or []) if p.permission_level and p.permission_level.value in ("CAN_EDIT", "CAN_MANAGE")})
    print(f"  코드 폴더 쓰기 권한: {', '.join(writers) or '없음'}")
    if any(name in ("users", "account users") for name in writers):
        print("  aops: 모든 사용자가 서버 코드를 고칠 수 있습니다. 폴더 권한을 확인하세요")

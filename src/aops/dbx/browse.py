"""What the requester can use, read with their own token (Databricks Apps user authorization).

The new-agent screen picks from these instead of typing names:
- Unity Catalog: catalogs → schemas → tables. Unity Catalog lists only the tables the requester may SELECT
  (or owns), with USE CATALOG and USE SCHEMA on the way; tables they can only BROWSE are left out, since a
  Genie space made with their token could not read them.
- SQL warehouses the requester can see, for the dev Genie space.
- Genie spaces the requester can open, to copy or move one into an agent folder.

The server never lists these as itself: what the requester cannot use, the screen does not offer. The one
check it makes as itself is whether the aops service principal can read a picked table: the production
Genie space runs with the service principal's rights, so a table it cannot SELECT stops the first deploy.
"""

from __future__ import annotations

import urllib.parse
from typing import Any

from .base import Caller

CATALOG_SCOPE = "catalog.catalogs:read"
SCHEMA_SCOPE = "catalog.schemas:read"
TABLE_SCOPE = "catalog.tables:read"
WAREHOUSE_SCOPE = "sql:read"
GENIE_SCOPE = "genie"
HIDDEN_CATALOGS = ("INTERNAL_CATALOG",)  # __databricks_internal and the like
READS = {"SELECT", "ALL_PRIVILEGES", "ALL PRIVILEGES"}
METRIC_VIEW = "METRIC_VIEW"
MAX_ITEMS = 1000
MAX_PAGES = 50  # Unity Catalog can return a few items per page; stop a runaway listing


def table_kind(table_type: str | None) -> str:
    """Where a Genie definition keeps it: `metric_views` for a metric view, `tables` for the rest."""
    return "metric_view" if (table_type or "").upper() == METRIC_VIEW else "table"


class BrowseMixin:
    """Service methods that list Unity Catalog objects, warehouses and Genie spaces as the requester."""

    def _user_pages(self, caller: Caller, path: str, params: dict[str, str], key: str, what: str, scope: str,
                    limit: int = MAX_ITEMS) -> tuple[list[dict[str, Any]], bool]:
        """Every page of a list call (the token is `next_page_token`); (items, truncated)."""
        items: list[dict[str, Any]] = []
        token = None
        for _ in range(MAX_PAGES):
            query = {**params, **({"page_token": token} if token else {})}
            body = self._user_api(caller, "GET", f"{path}?{urllib.parse.urlencode(query)}", None, what,
                                  scope=scope, command="aops init", code="list_failed")
            items += body.get(key) or []
            token = body.get("next_page_token")
            if not token or len(items) >= limit:
                break
        return items[:limit], bool(token) or len(items) > limit

    def my_catalogs(self, caller: Caller) -> dict[str, Any]:
        rows, truncated = self._user_pages(caller, "/api/2.1/unity-catalog/catalogs", {"max_results": "100"}, "catalogs",
                                           "카탈로그 목록", CATALOG_SCOPE)
        items = [{"name": c["name"], "comment": c.get("comment") or None, "type": c.get("catalog_type")}
                 for c in rows if c.get("catalog_type") not in HIDDEN_CATALOGS]
        items.sort(key=lambda c: (c["type"] == "SYSTEM_CATALOG", c["name"]))  # system and samples last
        return {"items": items, "truncated": truncated}

    def my_schemas(self, caller: Caller, catalog: str) -> dict[str, Any]:
        rows, truncated = self._user_pages(caller, "/api/2.1/unity-catalog/schemas", {"catalog_name": catalog, "max_results": "100"},
                                           "schemas", "스키마 목록", SCHEMA_SCOPE)
        items = sorted(({"name": s["name"], "full_name": s.get("full_name") or f"{catalog}.{s['name']}", "comment": s.get("comment") or None}
                        for s in rows if s.get("name") != "information_schema"), key=lambda s: s["name"])
        return {"items": items, "truncated": truncated}

    def my_tables(self, caller: Caller, catalog: str, schema: str) -> dict[str, Any]:
        rows, truncated = self._user_pages(
            caller, "/api/2.1/unity-catalog/tables",
            {"catalog_name": catalog, "schema_name": schema, "max_results": "200", "omit_columns": "true", "omit_properties": "true"},
            "tables", "테이블 목록", TABLE_SCOPE)
        items = sorted(({"name": t["name"], "full_name": t.get("full_name") or f"{catalog}.{schema}.{t['name']}",
                         "table_type": t.get("table_type"), "kind": table_kind(t.get("table_type")), "comment": t.get("comment") or None}
                        for t in rows), key=lambda t: t["name"])
        return {"items": items, "truncated": truncated}

    def my_table(self, caller: Caller, full_name: str) -> dict[str, Any]:
        """One table's columns, to tell tables apart before picking."""
        body = self._user_api(caller, "GET", f"/api/2.1/unity-catalog/tables/{urllib.parse.quote(full_name, safe='')}"
                              "?include_browse=false", None, "테이블 정보", scope=TABLE_SCOPE, command="aops init", code="list_failed")
        return {"full_name": body.get("full_name") or full_name, "table_type": body.get("table_type"), "kind": table_kind(body.get("table_type")),
                "comment": body.get("comment") or None, "owner": body.get("owner"),
                "columns": [{"name": c.get("name"), "type": c.get("type_text") or c.get("type_name"), "comment": c.get("comment") or None}
                            for c in body.get("columns") or []]}

    def my_warehouses(self, caller: Caller) -> dict[str, Any]:
        body = self._user_api(caller, "GET", "/api/2.0/sql/warehouses", None, "SQL warehouse 목록", scope=WAREHOUSE_SCOPE,
                              command="aops init", code="list_failed")
        default = getattr(getattr(self, "config", None), "warehouse_id", None)
        if caller.host and hasattr(self, "caller_workspace"):  # another workspace: its own warehouse is the default
            ws = self.caller_workspace(caller)
            default = default if ws.home else ws.warehouse_id
        items = sorted(({"id": w["id"], "name": w.get("name") or w["id"], "state": w.get("state"), "size": w.get("cluster_size"),
                         "serverless": bool(w.get("enable_serverless_compute")), "default": w["id"] == default}
                        for w in body.get("warehouses") or []), key=lambda w: (not w["default"], not w["serverless"], w["name"]))
        return {"items": items, "default": default}

    def service_table_access(self, caller: Caller, tables: list[str]) -> list[dict[str, Any]]:
        """Per picked table: can the aops service principal SELECT it (ok · missing · unknown)? Only for tables the
        requester can see with their own token, so the check tells nothing about tables hidden from them."""
        me = self._service_user()
        out = []
        for name in list(dict.fromkeys(tables))[:30]:
            quoted = urllib.parse.quote(name, safe="")
            mine = (self._as_user(caller.token, "GET", f"/api/2.1/unity-catalog/tables/{quoted}?include_browse=false", host=caller.host)
                    if caller.token else {"status": 401})
            if mine.get("status") != 200:
                out.append({"full_name": name, "status": "unknown", "detail": "내 권한으로 볼 수 없는 테이블이라 확인하지 않았습니다"})
                continue
            try:
                body = self.w.api_client.do("GET", f"/api/2.1/unity-catalog/effective-permissions/table/{quoted}", query={"principal": me}) or {}
            except Exception:
                out.append({"full_name": name, "status": "unknown", "detail": "aops 서비스 주체의 권한을 확인하지 못했습니다 (데이터 주인에게 SELECT를 확인하세요)"})
                continue
            privileges = sorted({p.get("privilege") for a in body.get("privilege_assignments") or [] for p in a.get("privileges") or []
                                 if p.get("privilege")})
            ok = bool(READS & set(privileges))
            out.append({"full_name": name, "status": "ok" if ok else "missing",
                        "detail": "aops 서비스 주체가 SELECT할 수 있습니다" if ok else
                        "aops 서비스 주체에 SELECT가 없습니다. 운영용 Genie는 서비스 주체 권한으로 만들고 돌기 때문에, 첫 배포 전에 데이터 주인이 "
                        "SELECT(와 USE CATALOG·USE SCHEMA)를 주어야 합니다"})
        return out

    def my_genie_spaces(self, caller: Caller, query: str | None = None) -> dict[str, Any]:
        rows, truncated = self._user_pages(caller, "/api/2.0/genie/spaces", {"page_size": "100"}, "spaces", "Genie 스페이스 목록",
                                           GENIE_SCOPE, limit=500)
        q = (query or "").strip().lower()
        items = [{"space_id": s["space_id"], "title": s.get("title") or s["space_id"], "description": s.get("description") or None,
                  "warehouse_id": s.get("warehouse_id")}
                 for s in rows if not q or q in f"{s.get('title') or ''} {s.get('description') or ''} {s['space_id']}".lower()]
        return {"items": sorted(items, key=lambda s: s["title"].lower()), "truncated": truncated}

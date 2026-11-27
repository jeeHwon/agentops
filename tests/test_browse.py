"""The new-agent screen's lists: what the requester can use, read with their own token."""

import json
import urllib.parse
from types import SimpleNamespace

import pytest

pytest.importorskip("databricks.sdk")  # the dbx extra
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from aops.dbx import nocode  # noqa: E402
from aops.dbx.service import Caller, Service, ServiceError  # noqa: E402
from aops.server.api import browse as routes  # noqa: E402
from aops.server.app import create_app  # noqa: E402


def user_service(pages):
    """A Service whose requester calls answer from `pages`: {path: [body, body, ...]} (one body per page)."""
    svc = object.__new__(Service)
    svc.config = SimpleNamespace(warehouse_id="wh-default")
    calls = []

    def as_user(token, method, path, body=None, host=None):
        base, _, query = path.partition("?")
        calls.append((method, base, dict(urllib.parse.parse_qsl(query))))
        answers = pages[base]
        return answers.pop(0) if isinstance(answers, list) else answers

    svc._as_user = as_user
    return svc, calls


def ok(body):
    return {"status": 200, "body": body, "error": None}


ME = Caller("dev@x.com", token="t")


def test_catalogs_follow_every_page_and_hide_internal_ones():
    svc, calls = user_service({"/api/2.1/unity-catalog/catalogs": [
        ok({"catalogs": [{"name": "samples", "catalog_type": "SYSTEM_CATALOG"}, {"name": "__databricks_internal", "catalog_type": "INTERNAL_CATALOG"}],
            "next_page_token": "p2"}),
        ok({"catalogs": [{"name": "main", "catalog_type": "MANAGED_CATALOG", "comment": "회사 데이터"}]})]})
    out = svc.my_catalogs(ME)
    assert [c["name"] for c in out["items"]] == ["main", "samples"]  # system catalogs last, internal hidden
    assert out["items"][0]["comment"] == "회사 데이터" and out["truncated"] is False
    assert calls[1][2]["page_token"] == "p2"


def test_tables_are_the_ones_unity_catalog_lets_me_select_with_their_kind():
    svc, calls = user_service({"/api/2.1/unity-catalog/tables": ok({"tables": [
        {"name": "orders", "full_name": "main.sales.orders", "table_type": "MANAGED", "comment": "주문"},
        {"name": "revenue", "full_name": "main.sales.revenue", "table_type": "METRIC_VIEW"}]})})
    out = svc.my_tables(ME, "main", "sales")
    assert [(t["full_name"], t["kind"]) for t in out["items"]] == [("main.sales.orders", "table"), ("main.sales.revenue", "metric_view")]
    query = calls[0][2]
    assert query["catalog_name"] == "main" and query["schema_name"] == "sales" and query["omit_columns"] == "true"
    assert "include_browse" not in query  # BROWSE-only tables stay out (the dev Genie could not read them)


def test_schemas_leave_out_information_schema():
    svc, _ = user_service({"/api/2.1/unity-catalog/schemas": ok({"schemas": [
        {"name": "sales", "full_name": "main.sales"}, {"name": "information_schema", "full_name": "main.information_schema"}]})})
    assert [s["name"] for s in svc.my_schemas(ME, "main")["items"]] == ["sales"]


def test_a_long_listing_stops_and_says_so():
    page = ok({"tables": [{"name": f"t{i}", "full_name": f"c.s.t{i}"} for i in range(200)], "next_page_token": "more"})
    svc, calls = user_service({"/api/2.1/unity-catalog/tables": page})
    out = svc.my_tables(ME, "c", "s")
    assert len(out["items"]) == 1000 and out["truncated"] is True and len(calls) == 5


def test_warehouses_put_the_server_default_first():
    svc, _ = user_service({"/api/2.0/sql/warehouses": ok({"warehouses": [
        {"id": "a", "name": "zeta", "state": "RUNNING", "enable_serverless_compute": True},
        {"id": "wh-default", "name": "shared", "state": "STOPPED", "cluster_size": "Small"}]})})
    out = svc.my_warehouses(ME)
    assert [w["id"] for w in out["items"]] == ["wh-default", "a"] and out["items"][0]["default"] and out["default"] == "wh-default"


def test_genie_spaces_filter_by_title_or_description():
    svc, _ = user_service({"/api/2.0/genie/spaces": ok({"spaces": [
        {"space_id": "1" * 32, "title": "택시 운행", "description": "뉴욕 택시"}, {"space_id": "2" * 32, "title": "매출"}]})})
    assert [s["title"] for s in svc.my_genie_spaces(ME, "택시")["items"]] == ["택시 운행"]


def test_a_token_without_the_scope_says_how_to_fix_it():
    svc, _ = user_service({"/api/2.1/unity-catalog/catalogs": {"status": 403, "body": None,
                                                              "error": "Provided OAuth token does not have required scopes: catalog"}})
    with pytest.raises(ServiceError) as caught:
        svc.my_catalogs(ME)
    assert caught.value.code == "missing_scope" and "catalog.catalogs:read" in caught.value.message
    assert "쿠키" in caught.value.message  # a screen opened before the scope was added


def test_the_service_principal_access_of_picked_tables():
    svc = object.__new__(Service)
    svc._me = "sp-app-id"
    hidden = {"main.secret.t"}
    svc._as_user = lambda token, method, path, body=None, host=None: {"status": 404 if urllib.parse.unquote(path.split("/")[-1].split("?")[0]) in hidden else 200}
    answers = {
        "main.sales.orders": {"privilege_assignments": [{"principal": "sp-app-id", "privileges": [{"privilege": "SELECT", "inherited_from_type": "SCHEMA"}]}]},
        "main.hr.people": {"privilege_assignments": []},
    }

    def do(method, path, query=None, body=None):
        name = urllib.parse.unquote(path.rsplit("/", 1)[1])
        assert query == {"principal": "sp-app-id"}
        if name not in answers:
            raise RuntimeError("Principal does not have READ METADATA on Table")
        return answers[name]

    svc.w = SimpleNamespace(api_client=SimpleNamespace(do=do))
    rows = {r["full_name"]: r for r in svc.service_table_access(ME, ["main.sales.orders", "main.hr.people", "samples.x.y", "main.sales.orders",
                                                                      "main.secret.t"])}
    assert {k: v["status"] for k, v in rows.items()} == {"main.sales.orders": "ok", "main.hr.people": "missing", "samples.x.y": "unknown",
                                                          "main.secret.t": "unknown"}
    assert "내 권한으로 볼 수 없는" in rows["main.secret.t"]["detail"]  # nothing about a table hidden from me
    assert "READ METADATA" not in rows["samples.x.y"]["detail"]  # no raw Databricks error text


def test_a_new_space_keeps_metric_views_apart():
    space = nocode.initial_space(["main.s.b", "main.s.a"], "매출 질문", ["main.s.rev"])
    assert space["data_sources"] == {"tables": [{"identifier": "main.s.a"}, {"identifier": "main.s.b"}],
                                     "metric_views": [{"identifier": "main.s.rev"}]}
    assert nocode.sources(space) == {"main.s.a", "main.s.b", "main.s.rev"}
    assert "metric_views" not in nocode.initial_space(["main.s.a"], None)["data_sources"]


def test_routes_list_with_the_requester(monkeypatch):
    seen = []
    fake = SimpleNamespace(
        my_catalogs=lambda caller: seen.append(caller.email) or {"items": [{"name": "main", "type": "MANAGED_CATALOG"}], "truncated": False},
        my_tables=lambda caller, c, s: {"items": [{"name": "orders", "full_name": f"{c}.{s}.orders", "kind": "table"}], "truncated": False},
        my_genie_spaces=lambda caller, q: {"items": [], "truncated": False},
        service_table_access=lambda caller, tables: [{"full_name": t, "status": "ok", "detail": ""} for t in tables])
    monkeypatch.setattr(routes, "service_for", lambda command: fake)
    monkeypatch.setattr(routes, "caller_of", lambda request, svc: Caller("dev@x.com"))
    client = TestClient(create_app(edition="dbx"))
    assert client.get("/api/v1/me/catalogs").json()["items"][0]["name"] == "main" and seen == ["dev@x.com"]
    assert client.get("/api/v1/me/catalogs/main/schemas/sales/tables").json()["items"][0]["full_name"] == "main.sales.orders"
    assert client.get("/api/v1/genie/spaces?q=x").json() == {"items": [], "truncated": False}
    assert client.post("/api/v1/no-code/table-access", json={"tables": ["main.sales.orders"]}).json()[0]["status"] == "ok"
    assert json.loads(json.dumps(client.get("/api/v1/openapi.json").json()["paths"]["/api/v1/me/catalogs"]["get"]["x-aops-screen"]))

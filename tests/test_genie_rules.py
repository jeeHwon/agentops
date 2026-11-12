"""No-code definition checks on the measured serialized_space shape."""

import copy

from aops.dbx.genie_rules import definition_hash, format_step, lint

GOOD = {
    "version": 2,
    "data_sources": {"tables": [
        {"identifier": "main.shop.orders", "description": ["주문"], "column_configs": [
            {"column_name": "order_id", "enable_entity_matching": False},
            {"column_name": "customer_email", "exclude": True},
            {"column_name": "amount", "synonyms": ["금액"]}]},
        {"identifier": "main.shop.customers", "description": ["고객"], "column_configs": []}]},
    "instructions": {
        "text_instructions": [{"id": "t1", "content": ["## PURPOSE\n", "주문 분석\n", "## CONSTRAINTS\n", "Instructions you must follow when providing summaries\n"]}],
        "example_question_sqls": [{"id": f"e{i}", "question": ["q"], "sql": ["SELECT 1"]} for i in range(5)],
        "sql_snippets": {"measures": [{"id": "m1", "sql": ["SUM(orders.amount)"]}]},
        "join_specs": [{"id": "j1"}]},
    "benchmarks": {"questions": [{"id": f"b{i}", "question": ["q"], "answer": [{"format": "SQL", "content": ["SELECT 1"]}]} for i in range(5)]},
}


def ids(report, passed):
    return {r["id"] for r in report["results"] if r["passed"] is passed}


def test_a_well_built_space_passes_every_rule():
    report = lint(GOOD)
    assert report["errors"] == 0 and report["score"] == 1.0 and not ids(report, False)
    assert format_step(GOOD)["status"] == "passed"


def test_a_shown_pii_column_is_an_error():
    space = copy.deepcopy(GOOD)
    space["data_sources"]["tables"][0]["column_configs"][1]["exclude"] = False
    step = format_step(space)
    assert step["status"] == "failed" and "columns.pii_hidden" in step["detail"] and "customer_email" in step["detail"]


def test_uc_tags_find_pii_columns_the_definition_never_configured():
    report = lint(GOOD, pii_columns={"main.shop.customers.phone_no"})
    assert "columns.pii_hidden" in ids(report, False)


def test_a_sparse_space_fails_on_examples_and_benchmarks():
    sparse = {"version": 2, "data_sources": {"tables": [{"identifier": "c.s.t"}]},
              "instructions": {"text_instructions": [{"id": "t", "content": ["짧은 안내"]}]}}
    report = lint(sparse)
    assert {"examples.count", "benchmarks.min"} <= ids(report, False) and report["errors"] >= 2
    assert "joins.defined" not in {r["id"] for r in report["results"]}  # one table: not applicable


def test_policy_changes_levels_and_values():
    space = copy.deepcopy(GOOD)
    space["instructions"]["example_question_sqls"] = space["instructions"]["example_question_sqls"][:2]
    assert lint(space)["errors"] == 1
    assert lint(space, rules={"examples.count": {"min": 2}})["errors"] == 0
    assert lint(space, rules={"examples.count": {"level": "off"}})["errors"] == 0
    assert "sources.allowed" in ids(lint(GOOD, rules={"sources.allowed": {"level": "warn", "catalogs": ["other."]}}), False)


def test_the_hash_ignores_key_order():
    shuffled = {k: GOOD[k] for k in reversed(list(GOOD))}
    assert definition_hash(shuffled) == definition_hash(GOOD)


def test_the_server_self_check_reads_the_space_and_applies_the_policy():
    import json
    from types import SimpleNamespace

    import pytest

    pytest.importorskip("databricks.sdk")
    from aops.dbx.service import Caller, Service, ServiceError

    svc = object.__new__(Service)
    space = SimpleNamespace(title="sales", warehouse_id="w", serialized_space=json.dumps(GOOD))
    svc.w = SimpleNamespace(genie=SimpleNamespace(get_space=lambda space_id, include_serialized_space: space))
    svc.policy = lambda: (1, {"no_code": {"rules": {"examples.count": {"min": 10}}}})
    report = svc.genie_check(Caller("dev@x.com"), "0" * 32)
    assert report["status"] == "failed" and "examples.count" in report["detail"] and report["tables"] == ["main.shop.orders", "main.shop.customers"]
    with pytest.raises(ServiceError):
        svc.genie_check(Caller("dev@x.com"), "not-an-id")
    space.serialized_space = ""
    with pytest.raises(ServiceError) as empty:
        svc.genie_check(Caller("dev@x.com"), "0" * 32)
    assert empty.value.code == "no_definition"

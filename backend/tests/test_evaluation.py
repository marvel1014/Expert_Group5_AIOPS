"""场景库与评估测试：场景校验、回放隔离、准确率与可复现性。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.evaluation.scenarios import (
    ScenarioValidationError,
    load_scenario_file,
    validate_scenario,
)
from app.evaluation.service import (
    evaluation_cases,
    run_evaluation,
    serialize_evaluation,
)
from app.models import ReplayBatch, Scenario

EXPECTED_SCENARIOS = {
    "db-pool-exhausted",
    "network-partition",
    "disk-full",
    "cpu-overload",
    "service-crash",
    "config-error",
}


# ------------------------------ 场景库 ------------------------------


def test_scenario_list_and_detail(client):
    response = client.get("/api/v1/scenarios")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] >= 6
    ids = {item["id"] for item in payload["items"]}
    assert EXPECTED_SCENARIOS <= ids
    for item in payload["items"]:
        assert item["name"]
        assert item["category"]
        assert item["expected"].get("root_cause_category")
        assert item["expected"].get("root_cause_node")

    detail = client.get("/api/v1/scenarios/db-pool-exhausted")
    assert detail.status_code == 200
    data = detail.json()
    assert data["definition"]["alerts"]
    assert data["definition"]["logs"]
    assert data["definition"]["topology"]["nodes"]


def _minimal_scenario() -> dict:
    return {
        "id": "unit-test-scenario",
        "name": "单元测试场景",
        "category": "cpu_overload",
        "compression": 10,
        "topology": {
            "nodes": [{"id": "order-host-01", "kind": "host", "name": "order-host-01"}],
            "edges": [],
        },
        "alerts": [
            {
                "offset": 0,
                "host": "order-host-01",
                "service": None,
                "rule": "CPU usage 98%",
                "severity": "critical",
                "message": "cpu saturation",
            }
        ],
        "logs": [],
        "expected": {
            "root_cause_category": "cpu_overload",
            "root_cause_node": "order-host-01",
            "affected_nodes": ["order-host-01"],
        },
    }


def test_validate_scenario_accepts_valid():
    scenario = validate_scenario(_minimal_scenario(), source_file="unit.yaml")
    assert scenario["id"] == "unit-test-scenario"
    assert scenario["definition"]["alerts"][0]["node"] == "order-host-01"


def test_validate_scenario_missing_expected():
    data = _minimal_scenario()
    data.pop("expected")
    with pytest.raises(ScenarioValidationError) as exc_info:
        validate_scenario(data, source_file="missing.yaml")
    assert exc_info.value.details
    assert any("expected" in item for item in exc_info.value.details)


def test_validate_scenario_unknown_node():
    data = _minimal_scenario()
    data["alerts"][0]["host"] = "node-not-in-topology"
    data["alerts"][0]["service"] = None
    with pytest.raises(ScenarioValidationError) as exc_info:
        validate_scenario(data)
    assert any("不存在的节点" in item for item in exc_info.value.details)


def test_validate_scenario_unknown_expected_node():
    data = _minimal_scenario()
    data["expected"]["root_cause_node"] = "mysql-primary"
    with pytest.raises(ScenarioValidationError) as exc_info:
        validate_scenario(data)
    assert any("expected.root_cause_node" in item for item in exc_info.value.details)


def test_load_scenario_file_reports_yaml_error(tmp_path):
    path = tmp_path / "broken.yaml"
    path.write_text("id: broken\n  bad: [\n", encoding="utf-8")
    with pytest.raises(ScenarioValidationError):
        load_scenario_file(path)


# ------------------------------ 回放 ------------------------------


def test_replay_creates_isolated_batches(client):
    first = client.post("/api/v1/scenarios/db-pool-exhausted/replay", json={"compression": 10})
    second = client.post("/api/v1/scenarios/db-pool-exhausted/replay", json={"compression": 10})
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_data = first.json()
    second_data = second.json()
    assert first_data["status"] == "completed"
    assert first_data["scenario_id"] == "db-pool-exhausted"
    assert first_data["stats"]["alert_count"] > 0
    assert first_data["stats"]["log_count"] > 0
    assert first_data["stats"]["incident_ids"]
    assert first_data["id"] != second_data["id"]

    first_incidents = set(first_data["stats"]["incident_ids"])
    second_incidents = set(second_data["stats"]["incident_ids"])
    assert first_incidents.isdisjoint(second_incidents)

    incidents = client.get(
        "/api/v1/incidents", params={"batch_id": first_data["id"]}
    ).json()
    assert incidents["total"] == len(first_incidents)

    # 回放事件必须落在看板默认时间窗内（前端演示可见）
    overview = client.get("/api/v1/stats/overview").json()
    assert overview["alert_count"] >= 1
    assert overview["incident_count"] >= 1


def test_replay_unknown_scenario(client):
    response = client.post("/api/v1/scenarios/not-exists/replay", json={})
    assert response.status_code == 404


# ------------------------------ 评估 ------------------------------


def test_run_evaluation_full_and_reproducible(db_session):
    first = run_evaluation(db_session, seed=42)
    assert first.status == "completed"
    metrics = first.metrics
    assert metrics["total"] >= 6
    assert 0.0 <= metrics["top1_accuracy"] <= 1.0
    assert 0.0 <= metrics["top3_accuracy"] <= 1.0
    assert 0.0 <= metrics["node_accuracy"] <= 1.0
    assert metrics["avg_latency_ms"] >= 0
    assert metrics["avg_compression_ratio"] > 0

    cases = evaluation_cases(db_session, first.id)
    assert len(cases) == metrics["total"]
    assert all(case.error is None for case in cases)
    assert all(case.expected.get("root_cause_category") for case in cases)
    assert all(case.actual.get("category") for case in cases)
    # 内置场景的期望与 Mock 确定性诊断应完全一致
    assert metrics["top1_accuracy"] == 1.0
    assert metrics["node_accuracy"] == 1.0

    second = run_evaluation(db_session, seed=42)
    for key in ("total", "top1_accuracy", "top3_accuracy", "node_accuracy"):
        assert second.metrics[key] == metrics[key]


def test_evaluation_endpoint_and_export(client):
    created = client.post(
        "/api/v1/evaluations",
        json={"scenario_ids": ["db-pool-exhausted"], "seed": 7},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["id"]
    assert body["status"] in ("running", "completed")

    detail = client.get(f"/api/v1/evaluations/{body['id']}")
    assert detail.status_code == 200
    data = detail.json()
    if data["status"] != "completed":
        pytest.fail("评估未完成")
    assert data["metrics"]["total"] == 1
    assert data["metrics"]["top1_accuracy"] == 1.0
    assert len(data["cases"]) == 1
    case = data["cases"][0]
    assert case["scenario_id"] == "db-pool-exhausted"
    assert case["top1_hit"] is True
    assert case["node_hit"] is True

    listed = client.get("/api/v1/evaluations").json()
    assert listed["total"] >= 1
    assert any(item["id"] == body["id"] for item in listed["items"])

    exported = client.get(f"/api/v1/evaluations/{body['id']}/export")
    assert exported.status_code == 200
    assert f"evaluation-{body['id']}.json" in exported.headers["content-disposition"]
    payload = json.loads(exported.content.decode("utf-8"))
    assert payload["metrics"]["total"] == 1
    assert payload["cases"][0]["scenario_id"] == "db-pool-exhausted"

    missing = client.get("/api/v1/evaluations/eval-not-exists")
    assert missing.status_code == 404


def test_evaluation_counts_incorrect_case(db_session):
    source = db_session.get(Scenario, "db-pool-exhausted")
    assert source is not None
    wrong = Scenario(
        id="wrong-expectation",
        name="错误期望场景（测试用）",
        category=source.category,
        description=source.description,
        definition=source.definition,
        expected={
            "root_cause_category": "cpu_overload",
            "root_cause_node": "order-service",
            "affected_nodes": ["order-service"],
        },
        alert_count=source.alert_count,
        log_count=source.log_count,
        source_file="test-only.yaml",
    )
    db_session.add(wrong)
    db_session.commit()
    try:
        evaluation = run_evaluation(db_session, ["wrong-expectation"], seed=42)
        cases = evaluation_cases(db_session, evaluation.id)
        assert len(cases) == 1
        case = cases[0]
        assert case.top1_hit is False
        assert case.top3_hit is False
        assert case.node_hit is False
        assert case.actual.get("category") == "db_connection_pool_exhausted"
        assert case.expected.get("root_cause_category") == "cpu_overload"
        assert evaluation.metrics["top1_accuracy"] == 0.0
        assert evaluation.metrics["node_accuracy"] == 0.0
    finally:
        # 先清理回放批次（外键引用场景），再删除临时场景
        for batch in db_session.execute(
            select(ReplayBatch).where(ReplayBatch.scenario_id == wrong.id)
        ).scalars().all():
            db_session.delete(batch)
        db_session.flush()
        db_session.delete(wrong)
        db_session.commit()


def test_serialize_evaluation_shape(db_session):
    evaluation = run_evaluation(db_session, ["db-pool-exhausted"], seed=42)
    cases = evaluation_cases(db_session, evaluation.id)
    data = serialize_evaluation(evaluation, cases)
    for key in ("id", "status", "metrics", "cases", "created_at"):
        assert key in data
    assert data["metrics"]["total"] == 1
    assert data["cases"][0]["top1_hit"] is True

"""端到端集成测试：注入 → 关联 → 诊断 → 报告 → 场景评估。"""

from __future__ import annotations

import time


def _wait_for(client, url: str, *, timeout: float = 20.0, done_statuses=None):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        response = client.get(url)
        assert response.status_code == 200, response.text
        last = response.json()
        if done_statuses is None:
            return last
        if last.get("status") in done_statuses:
            return last
        time.sleep(0.2)
    raise AssertionError(f"timeout waiting for {url}: {last}")


def _first_incident(client):
    incidents = client.get("/api/v1/incidents").json()
    assert incidents["total"] >= 1, incidents
    return incidents["items"][0]


def test_diagnosis_report_flow(client):
    client.post(
        "/api/v1/simulate",
        json={
            "mode": "burst",
            "host": "db-host-01",
            "service": "mysql-primary",
            "count": 30,
            "rule": "MySQL connection pool exhausted",
            "severity": "critical",
        },
    )
    incident = _first_incident(client)

    response = client.post(f"/api/v1/incidents/{incident['id']}/diagnose")
    assert response.status_code == 200, response.text
    diagnosis_id = response.json()["diagnosis_id"]

    diagnosis = _wait_for(
        client,
        f"/api/v1/diagnoses/{diagnosis_id}",
        done_statuses={"succeeded", "degraded", "failed"},
    )
    assert diagnosis["status"] in {"succeeded", "degraded"}
    assert diagnosis["root_cause_category"]
    assert diagnosis["root_cause_node"]
    assert 0.0 <= diagnosis["confidence"] <= 1.0
    assert diagnosis["source"] in {"mock", "llm", "degraded"}
    assert diagnosis["model"]
    assert diagnosis["prompt_version"]

    fast = client.get(f"/api/v1/incidents/{incident['id']}/fast-diagnosis")
    assert fast.status_code == 200
    assert fast.json()["candidates"]

    report = client.post(
        f"/api/v1/incidents/{incident['id']}/reports",
        json={"diagnosis_id": diagnosis_id},
    )
    assert report.status_code in {200, 201}, report.text
    report_body = report.json()
    assert report_body["content"]["steps"]
    assert report_body["markdown"]

    export = client.get(f"/api/v1/reports/{report_body['id']}/export?format=markdown")
    assert export.status_code == 200
    assert "attachment" in export.headers.get("content-disposition", "")

    second = client.post(
        f"/api/v1/incidents/{incident['id']}/reports",
        json={"diagnosis_id": diagnosis_id},
    )
    assert second.status_code in {200, 201}
    assert second.json()["version"] == report_body["version"] + 1


def test_scenario_replay_and_evaluation(client):
    scenarios = client.get("/api/v1/scenarios").json()
    assert scenarios["total"] >= 6
    scenario_id = scenarios["items"][0]["id"]

    replay = client.post(
        f"/api/v1/scenarios/{scenario_id}/replay",
        json={"compression": 10, "seed": 42},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["stats"]["alert_count"] > 0

    started = client.post(
        "/api/v1/evaluations",
        json={"seed": 42},
    )
    assert started.status_code in {200, 201, 202}, started.text
    evaluation_id = started.json()["id"]
    evaluation = _wait_for(
        client,
        f"/api/v1/evaluations/{evaluation_id}",
        timeout=60,
        done_statuses={"completed", "failed"},
    )
    assert evaluation["status"] == "completed", evaluation
    metrics = evaluation["metrics"]
    assert metrics["total"] >= 6
    assert 0.0 <= metrics["top1_accuracy"] <= 1.0
    assert len(evaluation["cases"]) >= 6

    exported = client.get(f"/api/v1/evaluations/{evaluation_id}/export")
    assert exported.status_code == 200


def test_evaluation_is_deterministic(client):
    ids = []
    for _ in range(2):
        started = client.post("/api/v1/evaluations", json={"seed": 7})
        evaluation_id = started.json()["id"]
        evaluation = _wait_for(
            client,
            f"/api/v1/evaluations/{evaluation_id}",
            timeout=60,
            done_statuses={"completed", "failed"},
        )
        assert evaluation["status"] == "completed"
        ids.append(evaluation["metrics"])
    assert ids[0]["top1_accuracy"] == ids[1]["top1_accuracy"]
    assert ids[0]["top3_accuracy"] == ids[1]["top3_accuracy"]
    assert ids[0]["node_accuracy"] == ids[1]["node_accuracy"]

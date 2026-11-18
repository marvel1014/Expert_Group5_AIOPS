"""告警/日志接入验收测试。"""

from __future__ import annotations

from datetime import timedelta

from app.core.time import utcnow


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["llm_mode"] == "mock"


def test_webhook_single_alert(client):
    occurred = utcnow() - timedelta(seconds=20)
    response = client.post(
        "/api/v1/alerts/webhook",
        json={
            "host": "db-host-01",
            "service": "mysql-primary",
            "rule": "MySQL connection pool exhausted",
            "severity": "critical",
            "message": "Too many connections",
            "occurred_at": occurred.isoformat(),
        },
    )
    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] == 1
    assert body["duplicate"] is False
    alert = body["alert"]
    assert alert["rule_family"] == "db_connection"
    assert alert["repeat_count"] == 1

    fetched = client.get(f"/api/v1/alerts/{body['alert_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["host"] == "db-host-01"


def test_webhook_validation_error_shape(client):
    response = client.post("/api/v1/alerts/webhook", json={"rule": "missing host"})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"


def test_webhook_invalid_json(client):
    response = client.post(
        "/api/v1/alerts/webhook",
        content=b"{not-json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_batch_partial_failure(client):
    response = client.post(
        "/api/v1/alerts/webhook",
        json={
            "alerts": [
                {
                    "host": "web-01",
                    "service": "gateway",
                    "rule": "HTTP 5xx rate high",
                    "severity": "high",
                },
                {"rule": "missing host"},
                {
                    "host": "web-02",
                    "service": "gateway",
                    "rule": "HTTP 5xx rate high",
                    "severity": "high",
                },
            ]
        },
    )
    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] == 2
    assert body["rejected"] == 1
    assert body["results"][1]["accepted"] is False
    assert "host" in body["results"][1]["error"]


def test_idempotent_dedup(client):
    occurred = utcnow() - timedelta(seconds=30)
    payload = {
        "host": "db-host-01",
        "service": "mysql-primary",
        "rule": "MySQL connection pool exhausted 151/151",
        "severity": "critical",
        "occurred_at": occurred.isoformat(),
    }
    first = client.post("/api/v1/alerts/webhook", json=payload).json()
    second = client.post("/api/v1/alerts/webhook", json=payload).json()

    assert first["alert_id"] == second["alert_id"]
    assert second["duplicate"] is True
    alert = client.get(f"/api/v1/alerts/{first['alert_id']}").json()
    assert alert["repeat_count"] == 2


def test_alert_out_of_window_creates_new(client):
    payload = {
        "host": "db-host-01",
        "service": "mysql-primary",
        "rule": "MySQL connection pool exhausted",
        "severity": "critical",
    }
    old = dict(payload, occurred_at=(utcnow() - timedelta(hours=2)).isoformat())
    new = dict(payload, occurred_at=(utcnow() - timedelta(minutes=1)).isoformat())
    first = client.post("/api/v1/alerts/webhook", json=old).json()
    second = client.post("/api/v1/alerts/webhook", json=new).json()
    assert first["alert_id"] != second["alert_id"]


def test_logs_ingest_and_query(client):
    trace_id = "trace-test-1"
    response = client.post(
        "/api/v1/logs",
        json={
            "host": "db-host-01",
            "service": "mysql-primary",
            "level": "ERROR",
            "message": "TooManyConnectionsError",
            "stack": "Traceback (most recent call last): ...",
            "trace_id": trace_id,
        },
    )
    assert response.status_code == 202
    assert response.json()["accepted"] == 1

    logs = client.get("/api/v1/logs", params={"trace_id": trace_id}).json()
    assert logs["count"] == 1
    assert "Traceback" in logs["items"][0]["stack"]

    empty = client.get("/api/v1/logs", params={"trace_id": "not-exist"}).json()
    assert empty["items"] == []


def test_alerts_filter_and_pagination(client):
    for index in range(3):
        client.post(
            "/api/v1/alerts/webhook",
            json={
                "host": f"web-{index}",
                "service": "gateway",
                "rule": f"rule {index}",
                "severity": "high",
            },
        )
    response = client.get("/api/v1/alerts", params={"host": "web-1"}).json()
    assert response["total"] == 1
    assert response["items"][0]["host"] == "web-1"

    paged = client.get("/api/v1/alerts", params={"page_size": 2}).json()
    assert len(paged["items"]) == 2
    assert paged["total"] == 3


def test_schema_indexes(client):
    from sqlalchemy import inspect

    from app.db import get_engine

    inspector = inspect(get_engine())
    alert_indexes = {index["name"] for index in inspector.get_indexes("alerts")}
    log_indexes = {index["name"] for index in inspector.get_indexes("logs")}
    assert "ix_alerts_fp_time" in alert_indexes
    assert "ix_alerts_cluster_id" in alert_indexes
    assert "ix_logs_host_time" in log_indexes
    assert "ix_alerts_dedup_key" in alert_indexes  # dedup_key 唯一索引

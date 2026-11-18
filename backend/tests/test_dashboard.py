"""看板统计与统一错误响应测试。"""

from __future__ import annotations


def test_overview_after_burst(client):
    client.post(
        "/api/v1/simulate",
        json={
            "mode": "burst",
            "host": "order-host-01",
            "service": "order-service",
            "count": 25,
            "rule": "CPU usage high",
            "severity": "high",
        },
    )
    overview = client.get("/api/v1/stats/overview").json()
    assert overview["alert_count"] == 25
    assert overview["incident_count"] == 1
    assert overview["compression_ratio"] == 25.0
    assert overview["severity_distribution"]["high"] == 25
    assert overview["topology_hit_rate"] == 0.0
    assert overview["generated_at"] is not None


def test_overview_empty(client):
    overview = client.get(
        "/api/v1/stats/overview",
        params={
            "start": "2030-01-01T00:00:00Z",
            "end": "2030-01-02T00:00:00Z",
        },
    ).json()
    assert overview["alert_count"] == 0
    assert overview["incident_count"] == 0
    assert overview["compression_ratio"] == 0.0
    assert overview["severity_distribution"] == {
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
    }


def test_not_found_error_shape(client):
    response = client.get("/api/v1/alerts/999999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"

    response = client.get("/api/v1/incidents/999999")
    assert response.status_code == 404


def test_pagination_page_size(client):
    for index in range(5):
        client.post(
            "/api/v1/alerts/webhook",
            json={
                "host": f"web-{index}",
                "service": "gateway",
                "rule": f"rule-{index}",
                "severity": "medium",
            },
        )
    page = client.get(
        "/api/v1/alerts", params={"page": 2, "page_size": 2}
    ).json()
    assert page["total"] == 5
    assert page["page"] == 2
    assert len(page["items"]) == 2


def test_simulate_burst_response(client):
    response = client.post(
        "/api/v1/simulate",
        json={"mode": "burst", "count": 7, "host": "web-01", "service": "gateway"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["alert_count"] == 7
    assert body["batch_id"].startswith("burst-")
    assert body["incident_ids"]


def test_simulate_invalid_mode(client):
    response = client.post("/api/v1/simulate", json={"mode": "unknown"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"

"""拓扑关联、根因候选与事件压缩测试。"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.errors import AppError
from app.core.time import utcnow
from app.topology.service import Topology


def _post(post_alert, *, host, service, rule, offset=0, severity="critical"):
    return post_alert(
        host=host,
        service=service,
        rule=rule,
        severity=severity,
        occurred_at=(utcnow() - timedelta(seconds=120) + timedelta(seconds=offset)).isoformat(),
    )


def test_topology_query(client):
    data = client.get("/api/v1/topology").json()
    node_ids = {node["id"] for node in data["nodes"]}
    assert {"mysql-primary", "order-service", "gateway"} <= node_ids
    edge = {"upstream": "mysql-primary", "downstream": "order-service"}
    assert any(
        item["upstream"] == edge["upstream"]
        and item["downstream"] == edge["downstream"]
        for item in data["edges"]
    )


def test_topology_reload(client):
    response = client.post("/api/v1/topology/reload")
    assert response.status_code == 200
    assert response.json()["nodes"]


def test_invalid_topology_rejected():
    with pytest.raises(AppError):
        Topology(
            nodes=[{"id": "a", "kind": "service"}],
            edges=[{"upstream": "a", "downstream": "missing"}],
        )
    with pytest.raises(AppError):
        Topology(nodes=[{"id": "a"}], edges=[{"upstream": "a", "downstream": "a"}])


def test_upstream_downstream_correlated(client, post_alert):
    _post(
        post_alert,
        host="db-host-01",
        service="mysql-primary",
        rule="MySQL connection pool exhausted 151/151",
        offset=0,
    )
    _post(
        post_alert,
        host="gateway-host-01",
        service="gateway",
        rule="HTTP 502 Bad Gateway",
        offset=5,
        severity="high",
    )
    incidents = client.get("/api/v1/incidents").json()
    assert incidents["total"] == 1
    incident = incidents["items"][0]
    assert set(incident["nodes"]) == {"mysql-primary", "gateway"}
    assert incident["topology_linked"] is True
    assert incident["root_candidates"][0]["node"] == "mysql-primary"
    assert incident["alert_count"] == 2


def test_no_topology_path_not_correlated(client, post_alert):
    _post(
        post_alert,
        host="legacy-host-a",
        service="legacy-a",
        rule="custom rule a",
    )
    _post(
        post_alert,
        host="legacy-host-b",
        service="legacy-b",
        rule="custom rule b",
    )
    incidents = client.get("/api/v1/incidents").json()
    assert incidents["total"] == 2
    assert all(item["topology_linked"] is False for item in incidents["items"])


def test_correlation_out_of_window_not_merged(client, post_alert):
    _post(
        post_alert,
        host="db-host-01",
        service="mysql-primary",
        rule="MySQL connection pool exhausted",
        offset=0,
    )
    _post(
        post_alert,
        host="gateway-host-01",
        service="gateway",
        rule="HTTP 502 Bad Gateway",
        offset=600,
        severity="high",
    )
    incidents = client.get("/api/v1/incidents").json()
    assert incidents["total"] == 2


def test_unknown_node_degrades(client, post_alert):
    _post(
        post_alert,
        host="legacy-host",
        service="legacy-app",
        rule="custom rule",
    )
    incidents = client.get("/api/v1/incidents").json()
    assert incidents["total"] == 1
    incident = incidents["items"][0]
    assert incident["topology_linked"] is False
    assert incident["nodes"][0].startswith("unknown:")


def test_incident_detail_and_actions(client, post_alert):
    data = _post(
        post_alert,
        host="db-host-01",
        service="mysql-primary",
        rule="MySQL connection pool exhausted",
    ).json()
    incident_id = client.get("/api/v1/incidents").json()["items"][0]["id"]
    detail = client.get(f"/api/v1/incidents/{incident_id}").json()
    assert detail["alerts"][0]["id"] == data["alert_id"]
    assert {"nodes", "edges"} <= set(detail["topology"].keys())

    acked = client.post(f"/api/v1/incidents/{incident_id}/ack").json()
    assert acked["status"] == "acked"
    resolved = client.post(f"/api/v1/incidents/{incident_id}/resolve").json()
    assert resolved["status"] == "resolved"


def test_compression_stats_after_burst(client):
    response = client.post(
        "/api/v1/simulate",
        json={
            "mode": "burst",
            "host": "order-host-01",
            "service": "order-service",
            "count": 20,
            "rule": "CPU usage high",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["alert_count"] == 20

    stats = client.get("/api/v1/stats/compression").json()
    assert stats["raw_alert_count"] == 20
    assert stats["incident_count"] == 1
    assert stats["compression_ratio"] == 20.0


def test_reachability_and_path():
    from app.topology.service import load_topology

    topology = load_topology()
    assert "gateway" in topology.reachable("mysql-primary")
    path = topology.path("mysql-primary", "gateway")
    assert path is not None and path[0] == "mysql-primary" and path[-1] == "gateway"
    assert topology.path("gateway", "mysql-primary") is None
    assert topology.related("mysql-primary", "gateway") is True
    assert topology.related("mysql-primary", "legacy-app") is False

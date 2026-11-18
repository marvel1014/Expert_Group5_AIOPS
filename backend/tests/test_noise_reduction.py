"""降噪聚类验收测试。"""

from __future__ import annotations

from datetime import timedelta

from app.core.config import settings
from app.core.time import floor_to_window, utcnow


def _base():
    """保证落在同一个窗口内的过去时间点。"""
    return floor_to_window(utcnow() - timedelta(seconds=120), settings.cluster_window_seconds) + timedelta(
        seconds=10
    )


def _post(post_alert, *, rule, host="db-host-01", service="mysql-primary", offset=0, **extra):
    return post_alert(
        host=host,
        service=service,
        rule=rule,
        occurred_at=(_base() + timedelta(seconds=offset)).isoformat(),
        **extra,
    ).json()


def test_same_host_rule_window_merges(client, post_alert):
    for index in range(3):
        _post(post_alert, rule=f"CPU usage high {90 + index}%", offset=index)
    clusters = client.get("/api/v1/clusters").json()
    assert clusters["total"] == 1
    cluster = clusters["items"][0]
    assert cluster["member_count"] == 3
    assert cluster["status"] == "open"
    assert cluster["merge_reason"]["policy"] == "host+rule+window"


def test_different_rules_not_merged(client, post_alert):
    _post(post_alert, rule="CPU usage high")
    _post(post_alert, rule="Disk usage high", offset=1)
    clusters = client.get("/api/v1/clusters").json()
    assert clusters["total"] == 2


def test_different_hosts_not_merged(client, post_alert):
    _post(post_alert, rule="CPU usage high")
    _post(post_alert, rule="CPU usage high", host="order-host-01", offset=1)
    clusters = client.get("/api/v1/clusters").json()
    assert clusters["total"] == 2


def test_window_expiry_not_merged(client, post_alert):
    _post(post_alert, rule="CPU usage high", offset=0)
    _post(
        post_alert,
        rule="CPU usage high",
        offset=settings.cluster_window_seconds + 60,
    )
    clusters = client.get("/api/v1/clusters").json()
    assert clusters["total"] == 2


def test_cluster_members_and_ack_resolve(client, post_alert):
    data = _post(post_alert, rule="Disk usage high 95%")
    cluster_id = data["alert"]["cluster_id"]
    detail = client.get(f"/api/v1/clusters/{cluster_id}").json()
    assert len(detail["member_alert_ids"]) == 1
    assert detail["members"][0]["repeat_count"] == 1

    acked = client.post(f"/api/v1/clusters/{cluster_id}/ack").json()
    assert acked["status"] == "acked"
    resolved = client.post(f"/api/v1/clusters/{cluster_id}/resolve").json()
    assert resolved["status"] == "resolved"
    assert resolved["resolved_at"] is not None


def test_noise_stats(client, post_alert):
    for index in range(4):
        _post(post_alert, rule=f"CPU usage high {80 + index}%", offset=index)
    stats = client.get("/api/v1/stats/noise").json()
    assert stats["raw_alert_count"] == 4
    assert stats["cluster_count"] == 1
    assert stats["compression_ratio"] == 4.0
    assert stats["top_hosts"][0]["host"] == "db-host-01"


def test_noise_stats_empty_window(client):
    start = (utcnow() + timedelta(days=1)).isoformat()
    end = (utcnow() + timedelta(days=2)).isoformat()
    stats = client.get(
        "/api/v1/stats/noise", params={"start": start, "end": end}
    ).json()
    assert stats["raw_alert_count"] == 0
    assert stats["cluster_count"] == 0
    assert stats["compression_ratio"] == 0.0


def test_fingerprint_normalization():
    from app.dedup.service import normalize_rule, rule_fingerprint

    assert normalize_rule("CPU usage is 97%") == normalize_rule("CPU usage is 91%")
    assert rule_fingerprint("webhook", "h1", "Disk usage 92%") == rule_fingerprint(
        "webhook", "h1", "Disk usage 88%"
    )
    assert rule_fingerprint("webhook", "h1", "Disk usage 92%") != rule_fingerprint(
        "webhook", "h1", "CPU usage 92%"
    )
    assert rule_fingerprint("webhook", "h1", "Disk usage") != rule_fingerprint(
        "webhook", "h2", "Disk usage"
    )


def test_bucket_boundary_merge(client, post_alert):
    """跨时间桶边界但仍在窗口内的同一故障应并入同一聚合。"""
    boundary = floor_to_window(
        utcnow() - timedelta(seconds=200), settings.cluster_window_seconds
    ) + timedelta(seconds=settings.cluster_window_seconds - 5)
    post_alert(
        rule="Network packet loss 30%",
        occurred_at=boundary.isoformat(),
    )
    post_alert(
        rule="Network packet loss 35%",
        occurred_at=(boundary + timedelta(seconds=20)).isoformat(),
    )
    clusters = client.get("/api/v1/clusters").json()
    assert clusters["total"] == 1
    assert clusters["items"][0]["member_count"] == 2


def test_cluster_auto_resolve_and_reopen(client, post_alert):
    from app.core.time import utcnow
    from app.db import session_scope
    from app.dedup.service import sweep_clusters
    from app.models import AlertCluster

    data = _post(post_alert, rule="Memory usage high 95%")
    cluster_id = data["alert"]["cluster_id"]

    with session_scope() as db:
        cluster = db.get(AlertCluster, cluster_id)
        cluster.last_seen = utcnow() - timedelta(
            seconds=settings.cluster_cooldown_seconds + 60
        )
    with session_scope() as db:
        assert sweep_clusters(db) >= 1
    assert client.get(f"/api/v1/clusters/{cluster_id}").json()["status"] == "resolved"

    reopened = _post(post_alert, rule="Memory usage high 93%", offset=1)
    assert reopened["alert"]["cluster_id"] == cluster_id
    detail = client.get(f"/api/v1/clusters/{cluster_id}").json()
    assert detail["status"] == "open"
    assert detail["reopen_count"] == 1

"""降噪聚类：规则归一化、指纹、固定时间桶聚合与生命周期管理。"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.time import as_utc, floor_to_window, utcnow
from app.models import Alert, AlertCluster

SEVERITY_ORDER = {"info": 1, "low": 2, "medium": 3, "high": 4, "critical": 5}
SEVERITY_ALIASES = {
    "p1": "critical",
    "fatal": "critical",
    "emergency": "critical",
    "严重": "critical",
    "紧急": "critical",
    "p2": "high",
    "error": "high",
    "major": "high",
    "p3": "medium",
    "warn": "medium",
    "warning": "medium",
    "p4": "low",
    "notice": "low",
    "info": "low",
    "debug": "low",
}

_DYNAMIC_RE = re.compile(r"\b[0-9a-f]{8,}\b|\d+(?:\.\d+)?%?", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def normalize_severity(value: str | None) -> str:
    text = (value or "medium").strip().lower()
    if text in SEVERITY_ORDER:
        return text
    return SEVERITY_ALIASES.get(text, "medium")


def severity_rank(value: str | None) -> int:
    return SEVERITY_ORDER.get(normalize_severity(value), 3)


def max_severity(a: str, b: str) -> str:
    return a if severity_rank(a) >= severity_rank(b) else b


def normalize_rule(rule: str) -> str:
    """剥离实例号、动态数值与多余空白，使同规则不同参数得到同一归一化文本。"""
    text = (rule or "").strip().lower()
    text = _DYNAMIC_RE.sub("#", text)
    text = _WS_RE.sub(" ", text).strip(" .:-")
    return text or "unknown"


def rule_fingerprint(source: str, host: str, rule: str) -> str:
    raw = f"{(source or 'webhook').strip().lower()}|{(host or '').strip().lower()}|{normalize_rule(rule)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@lru_cache
def _rule_families() -> dict[str, tuple[str, ...]]:
    path = settings.path(settings.rules_file)
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    families = data.get("families") or {}
    return {
        str(name): tuple(str(k).lower() for k in keywords or [])
        for name, keywords in families.items()
    }


def rule_family(rule: str) -> str:
    text = (rule or "").lower()
    for family, keywords in _rule_families().items():
        if any(keyword in text for keyword in keywords):
            return family
    return "other"


def dedup_key(fingerprint: str, occurred_at: datetime) -> str:
    window = floor_to_window(occurred_at, settings.dedup_window_seconds)
    return f"{fingerprint}:{int(window.timestamp())}"


def _reopen_if_resolved(cluster: AlertCluster, now: datetime) -> None:
    if cluster.status == "resolved":
        cluster.status = "open"
        cluster.reopen_count += 1
        cluster.resolved_at = None


def upsert_cluster(
    db: Session,
    alert: Alert,
    *,
    occurred_at: datetime,
    window_seconds: int | None = None,
) -> AlertCluster:
    """为新告警找到/创建聚合告警；支持当前桶与相邻上一桶的边界合并。"""
    window = window_seconds or settings.cluster_window_seconds
    occurred = as_utc(occurred_at)
    bucket = floor_to_window(occurred, window)

    cluster = db.execute(
        select(AlertCluster).where(
            AlertCluster.fingerprint == alert.fingerprint,
            AlertCluster.window_start == bucket,
        )
    ).scalar_one_or_none()

    if cluster is None:
        prev_bucket = bucket - timedelta(seconds=window)
        previous = db.execute(
            select(AlertCluster)
            .where(
                AlertCluster.fingerprint == alert.fingerprint,
                AlertCluster.window_start == prev_bucket,
            )
            .order_by(AlertCluster.id.desc())
        ).scalars().first()
        if previous is not None and occurred - as_utc(previous.last_seen) <= timedelta(
            seconds=window
        ):
            cluster = previous

    if cluster is None:
        cluster = AlertCluster(
            fingerprint=alert.fingerprint,
            window_start=bucket,
            host=alert.host,
            service=alert.service,
            rule=alert.rule,
            rule_family=alert.rule_family,
            severity=alert.severity,
            status="open",
            member_count=0,
            first_seen=occurred,
            last_seen=occurred,
            merge_reason={
                "policy": "host+rule+window",
                "window_seconds": window,
                "host": alert.host,
                "rule_family": alert.rule_family,
            },
        )
        db.add(cluster)
        db.flush()
    else:
        _reopen_if_resolved(cluster, utcnow())

    cluster.member_count += 1
    cluster.last_seen = max(as_utc(cluster.last_seen), occurred)
    cluster.severity = max_severity(cluster.severity, alert.severity)
    return cluster


def sweep_clusters(db: Session) -> int:
    """把冷却期内没有新成员告警的聚合告警标记为已解决。"""
    now = utcnow()
    deadline = now - timedelta(seconds=settings.cluster_cooldown_seconds)
    clusters = db.execute(
        select(AlertCluster).where(
            AlertCluster.status.in_(["open", "acked"]),
            AlertCluster.last_seen < deadline,
        )
    ).scalars().all()
    for cluster in clusters:
        cluster.status = "resolved"
        cluster.resolved_at = now
    return len(clusters)


def ack_cluster(db: Session, cluster_id: int) -> AlertCluster | None:
    cluster = db.get(AlertCluster, cluster_id)
    if cluster and cluster.status == "open":
        cluster.status = "acked"
    return cluster


def resolve_cluster(db: Session, cluster_id: int) -> AlertCluster | None:
    cluster = db.get(AlertCluster, cluster_id)
    if cluster and cluster.status != "resolved":
        cluster.status = "resolved"
        cluster.resolved_at = utcnow()
    return cluster


def cluster_to_dict(cluster: AlertCluster, *, member_ids: list[int] | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": cluster.id,
        "fingerprint": cluster.fingerprint,
        "host": cluster.host,
        "service": cluster.service,
        "rule": cluster.rule,
        "rule_family": cluster.rule_family,
        "severity": cluster.severity,
        "status": cluster.status,
        "member_count": cluster.member_count,
        "first_seen": cluster.first_seen,
        "last_seen": cluster.last_seen,
        "resolved_at": cluster.resolved_at,
        "reopen_count": cluster.reopen_count,
        "merge_reason": cluster.merge_reason,
    }
    if member_ids is not None:
        data["member_alert_ids"] = member_ids
    return data


def list_clusters(
    db: Session,
    *,
    host: str | None = None,
    service: str | None = None,
    status: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[AlertCluster], int]:
    conditions = []
    if host:
        conditions.append(AlertCluster.host == host)
    if service:
        conditions.append(AlertCluster.service == service)
    if status:
        conditions.append(AlertCluster.status == status)
    if start:
        conditions.append(AlertCluster.last_seen >= start)
    if end:
        conditions.append(AlertCluster.first_seen <= end)

    base = select(AlertCluster).where(*conditions)
    total = db.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar_one()
    items = db.execute(
        base.order_by(AlertCluster.last_seen.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()
    return list(items), total


def cluster_members(db: Session, cluster_id: int) -> list[Alert]:
    return list(
        db.execute(
            select(Alert)
            .where(Alert.cluster_id == cluster_id)
            .order_by(Alert.occurred_at.asc())
        ).scalars().all()
    )


def noise_stats(
    db: Session, start: datetime, end: datetime
) -> dict[str, Any]:
    rows = db.execute(
        select(Alert.host, Alert.rule_family, func.sum(Alert.repeat_count))
        .where(Alert.occurred_at >= start, Alert.occurred_at <= end)
        .group_by(Alert.host, Alert.rule_family)
    ).all()
    raw_count = sum(int(row[2] or 0) for row in rows)
    cluster_count = db.execute(
        select(func.count(AlertCluster.id)).where(
            AlertCluster.first_seen >= start, AlertCluster.first_seen <= end
        )
    ).scalar_one()
    by_host: dict[str, int] = {}
    by_family: dict[str, int] = {}
    for host, family, count in rows:
        by_host[host] = by_host.get(host, 0) + int(count or 0)
        by_family[family or "other"] = by_family.get(family or "other", 0) + int(
            count or 0
        )
    compression = round(raw_count / cluster_count, 2) if cluster_count else 0.0
    return {
        "window": {"start": start, "end": end},
        "raw_alert_count": raw_count,
        "cluster_count": cluster_count,
        "compression_ratio": compression,
        "top_hosts": [
            {"host": host, "raw_count": count}
            for host, count in sorted(by_host.items(), key=lambda kv: -kv[1])[:5]
        ],
        "top_rule_families": [
            {"rule_family": family, "raw_count": count}
            for family, count in sorted(by_family.items(), key=lambda kv: -kv[1])[:5]
        ],
    }

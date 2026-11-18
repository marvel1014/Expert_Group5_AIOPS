"""拓扑载入、校验、可达性计算与拓扑感知的告警关联。"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Any

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.time import as_utc, utcnow
from app.dedup.service import SEVERITY_ORDER, severity_rank
from app.models import Alert, Incident, IncidentAlert
from app.ingestion.service import alert_to_dict

logger = logging.getLogger(__name__)

RISK_KEYWORDS = {
    "disk_full": ["no space", "disk", "filesystem", "inode"],
    "db_connection_pool_exhausted": [
        "too many connections",
        "connection pool",
        "deadlock",
    ],
    "network_partition": ["network", "packet loss", "connection reset", "dns"],
    "cpu_overload": ["cpu", "load average"],
    "service_crash": ["crash", "exited", "process", "restart"],
    "config_error": ["config", "invalid argument"],
}


class Topology:
    def __init__(self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]):
        self.nodes: dict[str, dict[str, Any]] = {}
        self.aliases: dict[str, str] = {}
        for node in nodes:
            node_id = str(node["id"])
            if node_id in self.nodes:
                raise AppError(
                    f"拓扑节点重复: {node_id}",
                    code="invalid_topology",
                    details={"node": node_id},
                )
            self.nodes[node_id] = node
            self.aliases[node_id.lower()] = node_id
            name = node.get("name")
            if name:
                self.aliases[str(name).lower()] = node_id

        self.edges: list[dict[str, Any]] = []
        self.adj: dict[str, list[str]] = {node_id: [] for node_id in self.nodes}
        self.rev: dict[str, list[str]] = {node_id: [] for node_id in self.nodes}
        for edge in edges:
            upstream = str(edge.get("upstream", ""))
            downstream = str(edge.get("downstream", ""))
            missing = [n for n in (upstream, downstream) if n not in self.nodes]
            if missing:
                raise AppError(
                    "拓扑依赖边引用不存在节点",
                    code="invalid_topology",
                    details={
                        "edge": edge,
                        "missing_nodes": missing,
                    },
                )
            if upstream == downstream:
                raise AppError(
                    "拓扑依赖边不能自环",
                    code="invalid_topology",
                    details={"edge": edge},
                )
            self.edges.append(
                {
                    "upstream": upstream,
                    "downstream": downstream,
                    "weight": float(edge.get("weight", 1.0)),
                }
            )
            self.adj[upstream].append(downstream)
            self.rev[downstream].append(upstream)

    def resolve(self, service: str | None, host: str | None) -> str | None:
        for value in (service, host):
            if value and value.strip().lower() in self.aliases:
                return self.aliases[value.strip().lower()]
        return None

    def reachable(self, start: str) -> set[str]:
        seen: set[str] = set()
        queue = deque([start])
        while queue:
            current = queue.popleft()
            for nxt in self.adj.get(current, []):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return seen

    def path(self, start: str, end: str) -> list[str] | None:
        if start == end:
            return [start]
        prev: dict[str, str | None] = {start: None}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            for nxt in self.adj.get(current, []):
                if nxt in prev:
                    continue
                prev[nxt] = current
                if nxt == end:
                    path = [nxt]
                    while prev[path[-1]] is not None:
                        path.append(prev[path[-1]])  # type: ignore[arg-type]
                    return list(reversed(path))
                queue.append(nxt)
        return None

    def related(self, a: str | None, b: str | None) -> bool:
        if a is None or b is None:
            return a == b
        if a == b:
            return True
        return self.path(a, b) is not None or self.path(b, a) is not None

    def upstream_of(self, node: str, candidates: set[str]) -> bool:
        """candidates 中是否存在可达 node 的上游节点。"""
        return any(
            other != node and node in self.reachable(other)
            for other in candidates
        )

    def connected(self, nodes: set[str]) -> bool:
        if len(nodes) <= 1:
            return False
        known = {n for n in nodes if n in self.nodes}
        if len(known) != len(nodes):
            return False
        start = next(iter(known))
        return all(
            other == start or self.related(start, other) for other in known
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [
                {"id": key, **value} for key, value in self.nodes.items()
            ],
            "edges": self.edges,
        }


_topology: Topology | None = None
_topology_mtime: float | None = None


def load_topology(force: bool = False) -> Topology:
    global _topology, _topology_mtime
    path = settings.path(settings.topology_file)
    if not path.exists():
        if _topology is not None and not force:
            return _topology
        _topology = Topology([], [])
        return _topology
    mtime = path.stat().st_mtime
    if force or _topology is None or _topology_mtime != mtime:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        _topology = Topology(data.get("nodes") or [], data.get("edges") or [])
        _topology_mtime = mtime
    return _topology


def topology_dict() -> dict[str, Any]:
    return load_topology().to_dict()


# ---------------------------- 事件关联 ----------------------------


def _alert_node(alert: Alert) -> str | None:
    topology = load_topology()
    resolved = topology.resolve(alert.service, alert.host)
    if resolved:
        return resolved
    fallback = (alert.service or alert.host or "").strip().lower()
    return f"unknown:{fallback}" if fallback else None


def _rule_hint(alerts: list[Alert]) -> tuple[float, list[str]]:
    text = " ".join(
        f"{a.rule} {a.message} {a.rule_family}".lower() for a in alerts
    )
    for category, keywords in RISK_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return 1.0, [category]
    return 0.0, []


def _node_rank_payload(
    node: str,
    node_alerts: list[Alert],
    topology: Topology,
    incident_nodes: set[str],
    *,
    known: bool,
) -> dict[str, Any]:
    earliest = min(as_utc(a.first_seen) for a in node_alerts)
    highest = max((severity_rank(a.severity) for a in node_alerts), default=3)
    hint_score, hints = _rule_hint(node_alerts)
    no_upstream = 0.0
    reasons: list[str] = []
    if known:
        if not topology.upstream_of(node, incident_nodes):
            no_upstream = 1.0
            reasons.append("拓扑入度为 0，无上游告警节点")
        else:
            reasons.append("存在上游告警节点")
    else:
        reasons.append("节点不在拓扑中，仅按告警特征排序")
    reasons.append(f"最早告警 {earliest.isoformat()}")
    reasons.append(f"最高严重级别指数 {highest}")
    if hints:
        reasons.append(f"命中规则化故障特征: {', '.join(hints)}")
    score = (
        0.55 * no_upstream
        + 0.25 * ((min(highest, 5) - 1) / 4.0)
        + 0.20 * hint_score
    )
    return {
        "node": node,
        "score": round(score, 4),
        "severity": max(
            (a.severity for a in node_alerts),
            key=lambda s: SEVERITY_ORDER.get(s, 3),
        ),
        "first_seen": earliest.isoformat(),
        "reasons": reasons,
    }


def _recompute_incident(db: Session, incident: Incident) -> None:
    links = db.execute(
        select(IncidentAlert.alert_id).where(
            IncidentAlert.incident_id == incident.id
        )
    ).scalars().all()
    alerts = (
        list(
            db.execute(
                select(Alert).where(Alert.id.in_(links))
            ).scalars().all()
        )
        if links
        else []
    )
    topology = load_topology()
    by_node: dict[str, list[Alert]] = {}
    for alert in alerts:
        node = _alert_node(alert) or "unknown"
        by_node.setdefault(node, []).append(alert)

    nodes = sorted(by_node)
    known_nodes = {n for n in nodes if n in topology.nodes}
    incident.nodes = nodes
    incident.alert_count = sum(a.repeat_count for a in alerts)
    incident.window_start = min(as_utc(a.first_seen) for a in alerts)
    incident.window_end = max(as_utc(a.last_seen) for a in alerts)
    incident.severity = max(
        (a.severity for a in alerts), key=lambda s: SEVERITY_ORDER.get(s, 3)
    )
    incident.topology_linked = topology.connected(known_nodes) and len(nodes) == len(
        known_nodes
    )

    candidates = [
        _node_rank_payload(
            node,
            node_alerts,
            topology,
            known_nodes,
            known=node in topology.nodes,
        )
        for node, node_alerts in by_node.items()
    ]
    candidates.sort(
        key=lambda item: (
            -item["score"],
            item["first_seen"],
            item["node"],
        )
    )
    incident.root_candidates = candidates
    if candidates:
        top = candidates[0]
        node_alerts = by_node.get(top["node"], [])
        first_rule = node_alerts[0].rule if node_alerts else "未知故障"
        incident.title = f"疑似 {top['node']} 故障：{first_rule}"
    else:
        incident.title = "未知故障事件"
    db.flush()


def _merge_incidents(db: Session, target: Incident, others: list[Incident]) -> None:
    for other in others:
        if other.id == target.id:
            continue
        links = db.execute(
            select(IncidentAlert).where(IncidentAlert.incident_id == other.id)
        ).scalars().all()
        for link in links:
            link.incident_id = target.id
        db.delete(other)
    db.flush()


def _find_candidate_incidents(
    db: Session, alert: Alert
) -> list[Incident]:
    topology = load_topology()
    node = _alert_node(alert)
    occurred = as_utc(alert.first_seen)
    window = timedelta(seconds=settings.correlation_window_seconds)
    incidents = db.execute(
        select(Incident).where(Incident.status.in_(["open", "acked"]))
    ).scalars().all()
    candidates: list[Incident] = []
    for incident in incidents:
        if abs(occurred - as_utc(incident.window_end)) > window:
            continue
        related = False
        for incident_node in incident.nodes or []:
            if node is None:
                continue
            if node.startswith("unknown:") or incident_node.startswith("unknown:"):
                related = node == incident_node
            else:
                related = topology.related(node, incident_node)
            if related:
                break
        if related:
            candidates.append(incident)
    return candidates


def correlate_pending_alerts(db: Session, alert_ids: list[int] | None = None) -> int:
    """把尚未归入事件的告警按拓扑 + 时间窗关联为故障事件；返回新建事件数。"""
    query = (
        select(Alert)
        .outerjoin(IncidentAlert, IncidentAlert.alert_id == Alert.id)
        .where(IncidentAlert.id.is_(None))
        .order_by(Alert.first_seen.asc())
    )
    if alert_ids is not None:
        if not alert_ids:
            return 0
        query = query.where(Alert.id.in_(alert_ids))
    pending = list(db.execute(query).scalars().all())

    created = 0
    for alert in pending:
        candidates = _find_candidate_incidents(db, alert)
        if candidates:
            target = sorted(candidates, key=lambda inc: inc.id)[0]
            if len(candidates) > 1:
                _merge_incidents(db, target, candidates[1:])
            db.add(IncidentAlert(incident_id=target.id, alert_id=alert.id))
            db.flush()
            _recompute_incident(db, target)
        else:
            incident = Incident(
                title="",
                status="open",
                severity=alert.severity,
                window_start=as_utc(alert.first_seen),
                window_end=as_utc(alert.last_seen),
                nodes=[],
                root_candidates=[],
                alert_count=0,
                topology_linked=False,
                batch_id=alert.batch_id,
            )
            db.add(incident)
            db.flush()
            db.add(IncidentAlert(incident_id=incident.id, alert_id=alert.id))
            db.flush()
            _recompute_incident(db, incident)
            created += 1
    db.commit()
    return created


def sweep_incidents(db: Session) -> int:
    now = utcnow()
    deadline = now - timedelta(seconds=settings.incident_cooldown_seconds)
    incidents = db.execute(
        select(Incident).where(
            Incident.status.in_(["open", "acked"]),
            Incident.window_end < deadline,
        )
    ).scalars().all()
    for incident in incidents:
        incident.status = "resolved"
    return len(incidents)


def incident_to_dict(
    incident: Incident, *, db: Session | None = None, detail: bool = False
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": incident.id,
        "title": incident.title,
        "status": incident.status,
        "severity": incident.severity,
        "window_start": incident.window_start,
        "window_end": incident.window_end,
        "nodes": incident.nodes or [],
        "root_candidates": incident.root_candidates or [],
        "alert_count": incident.alert_count,
        "topology_linked": incident.topology_linked,
        "batch_id": incident.batch_id,
        "created_at": incident.created_at,
    }
    if detail and db is not None:
        alert_ids = db.execute(
            select(IncidentAlert.alert_id).where(
                IncidentAlert.incident_id == incident.id
            )
        ).scalars().all()
        alerts = (
            db.execute(
                select(Alert)
                .where(Alert.id.in_(alert_ids))
                .order_by(Alert.first_seen.asc())
            ).scalars().all()
            if alert_ids
            else []
        )
        data["alerts"] = [alert_to_dict(a) for a in alerts]
        data["topology"] = topology_dict()
    return data


def list_incidents(
    db: Session,
    *,
    status: str | None = None,
    severity: str | None = None,
    batch_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[Incident], int]:
    conditions = []
    if status:
        conditions.append(Incident.status == status)
    if severity:
        conditions.append(Incident.severity == severity)
    if batch_id:
        conditions.append(Incident.batch_id == batch_id)
    if start:
        conditions.append(Incident.window_end >= start)
    if end:
        conditions.append(Incident.window_start <= end)
    base = select(Incident).where(*conditions)
    total = db.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar_one()
    items = db.execute(
        base.order_by(Incident.window_start.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()
    return list(items), total


def compression_stats(db: Session, start: datetime, end: datetime) -> dict[str, Any]:
    raw = db.execute(
        select(func.coalesce(func.sum(Alert.repeat_count), 0)).where(
            Alert.occurred_at >= start, Alert.occurred_at <= end
        )
    ).scalar_one()
    incidents = db.execute(
        select(func.count(Incident.id)).where(
            Incident.window_start >= start, Incident.window_start <= end
        )
    ).scalar_one()
    linked = db.execute(
        select(func.count(Incident.id)).where(
            Incident.window_start >= start,
            Incident.window_start <= end,
            Incident.topology_linked.is_(True),
        )
    ).scalar_one()
    raw = int(raw or 0)
    return {
        "window": {"start": start, "end": end},
        "raw_alert_count": raw,
        "incident_count": incidents,
        "compression_ratio": round(raw / incidents, 2) if incidents else 0.0,
        "topology_hit_rate": round(linked / incidents, 4) if incidents else 0.0,
    }

"""告警与日志接入服务：规范化、幂等去重、批量写入与查询。"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.time import as_utc, utcnow
from app.dedup.service import (
    dedup_key,
    max_severity,
    normalize_rule,
    normalize_severity,
    rule_family,
    rule_fingerprint,
    upsert_cluster,
)
from app.ingestion.schemas import AlertIn, LogIn
from app.models import Alert, LogEntry

logger = logging.getLogger(__name__)


def alert_to_dict(alert: Alert) -> dict[str, Any]:
    return {
        "id": alert.id,
        "fingerprint": alert.fingerprint,
        "host": alert.host,
        "service": alert.service,
        "rule": alert.rule,
        "rule_family": alert.rule_family,
        "severity": alert.severity,
        "message": alert.message,
        "labels": alert.labels or {},
        "occurred_at": alert.occurred_at,
        "first_seen": alert.first_seen,
        "last_seen": alert.last_seen,
        "repeat_count": alert.repeat_count,
        "cluster_id": alert.cluster_id,
        "status": alert.status,
        "batch_id": alert.batch_id,
    }


def log_to_dict(entry: LogEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "ts": entry.ts,
        "host": entry.host,
        "service": entry.service,
        "level": entry.level,
        "message": entry.message,
        "stack": entry.stack,
        "trace_id": entry.trace_id,
        "alert_id": entry.alert_id,
        "batch_id": entry.batch_id,
    }


def ingest_alert(db: Session, data: AlertIn) -> tuple[Alert, bool]:
    """写入单条告警；返回 (alert, 是否为重复告警)。"""
    occurred = as_utc(data.occurred_at or utcnow())
    source = (data.source or "webhook").strip()
    rule = data.rule.strip()
    fingerprint = rule_fingerprint(source, data.host, rule)
    key = dedup_key(fingerprint, occurred)

    existing = db.execute(
        select(Alert).where(Alert.dedup_key == key)
    ).scalar_one_or_none()

    if existing is not None:
        existing.repeat_count += 1
        existing.last_seen = max(as_utc(existing.last_seen), occurred)
        existing.severity = max_severity(existing.severity, data.severity)
        if data.message:
            existing.message = data.message
        alert, duplicate = existing, True
    else:
        alert = Alert(
            fingerprint=fingerprint,
            dedup_key=key,
            source=source,
            host=data.host,
            service=data.service,
            rule=rule,
            rule_family=rule_family(rule),
            severity=normalize_severity(data.severity),
            message=data.message,
            labels=data.labels or {},
            occurred_at=occurred,
            first_seen=occurred,
            last_seen=occurred,
            repeat_count=1,
            batch_id=data.batch_id,
        )
        db.add(alert)
        db.flush()
        duplicate = False

    cluster = upsert_cluster(db, alert, occurred_at=occurred)
    alert.cluster_id = cluster.id
    db.flush()
    return alert, duplicate


def ingest_alerts(
    db: Session, items: list[dict[str, Any]]
) -> dict[str, Any]:
    """批量写入，逐条返回受理结果；单条非法不影响其他条目。"""
    results: list[dict[str, Any]] = []
    accepted = duplicates = rejected = 0
    alert_ids: list[int] = []
    for index, raw in enumerate(items):
        try:
            data = AlertIn.model_validate(raw)
            alert, duplicate = ingest_alert(db, data)
        except ValidationError as exc:
            rejected += 1
            results.append(
                {
                    "index": index,
                    "accepted": False,
                    "error": "; ".join(
                        f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                        for err in exc.errors()
                    ),
                }
            )
            continue
        accepted += 1
        if duplicate:
            duplicates += 1
        alert_ids.append(alert.id)
        results.append(
            {
                "index": index,
                "accepted": True,
                "duplicate": duplicate,
                "alert_id": alert.id,
            }
        )
    db.commit()
    return {
        "accepted": accepted,
        "duplicates": duplicates,
        "rejected": rejected,
        "alert_ids": alert_ids,
        "results": results,
    }


def ingest_logs(db: Session, items: list[dict[str, Any]]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    accepted = rejected = 0
    for index, raw in enumerate(items):
        try:
            data = LogIn.model_validate(raw)
        except ValidationError as exc:
            rejected += 1
            results.append(
                {
                    "index": index,
                    "accepted": False,
                    "error": "; ".join(
                        f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                        for err in exc.errors()
                    ),
                }
            )
            continue
        entry = LogEntry(
            ts=as_utc(data.ts or utcnow()),
            host=data.host,
            service=data.service,
            level=(data.level or "ERROR").upper(),
            message=data.message,
            stack=data.stack,
            trace_id=data.trace_id,
            alert_id=data.alert_id,
            batch_id=data.batch_id,
        )
        db.add(entry)
        accepted += 1
        results.append({"index": index, "accepted": True})
    db.commit()
    return {"accepted": accepted, "rejected": rejected, "results": results}


def list_alerts(
    db: Session,
    *,
    host: str | None = None,
    service: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    batch_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[Alert], int]:
    conditions = []
    if host:
        conditions.append(Alert.host == host)
    if service:
        conditions.append(Alert.service == service)
    if severity:
        conditions.append(Alert.severity == normalize_severity(severity))
    if status:
        conditions.append(Alert.status == status)
    if batch_id:
        conditions.append(Alert.batch_id == batch_id)
    if start:
        conditions.append(Alert.last_seen >= start)
    if end:
        conditions.append(Alert.first_seen <= end)

    base = select(Alert).where(*conditions)
    total = db.execute(
        select(func.count()).select_from(base.subquery())
    ).scalar_one()
    items = db.execute(
        base.order_by(Alert.last_seen.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).scalars().all()
    return list(items), total


def list_logs(
    db: Session,
    *,
    host: str | None = None,
    service: str | None = None,
    trace_id: str | None = None,
    level: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 50,
) -> list[LogEntry]:
    conditions = []
    if host:
        conditions.append(LogEntry.host == host)
    if service:
        conditions.append(LogEntry.service == service)
    if trace_id:
        conditions.append(LogEntry.trace_id == trace_id)
    if level:
        conditions.append(LogEntry.level == level.upper())
    if start:
        conditions.append(LogEntry.ts >= start)
    if end:
        conditions.append(LogEntry.ts <= end)
    return list(
        db.execute(
            select(LogEntry)
            .where(*conditions)
            .order_by(LogEntry.ts.desc())
            .limit(min(limit, 500))
        ).scalars().all()
    )

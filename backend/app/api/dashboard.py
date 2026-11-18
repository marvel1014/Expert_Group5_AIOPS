"""看板统计路由：概览、压缩统计。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.time import as_utc, utcnow
from app.db import get_db
from app.models import Alert, Evaluation, Incident
from app.topology.service import compression_stats

router = APIRouter(prefix="/api/v1/stats", tags=["stats"])


def _window(
    start: datetime | None, end: datetime | None, window_minutes: int = 60
) -> tuple[datetime, datetime]:
    end_at = as_utc(end or utcnow())
    start_at = as_utc(start or (end_at - timedelta(minutes=window_minutes)))
    return start_at, end_at


@router.get("/compression")
def get_compression_stats(
    start: datetime | None = None,
    end: datetime | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    start_at, end_at = _window(start, end, 24 * 60)
    return compression_stats(db, start_at, end_at)


@router.get("/overview")
def get_overview(
    start: datetime | None = None,
    end: datetime | None = None,
    window_minutes: int = Query(60, ge=1, le=1440),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    start_at, end_at = _window(start, end, window_minutes)

    alert_count = int(
        db.execute(
            select(func.coalesce(func.sum(Alert.repeat_count), 0)).where(
                Alert.occurred_at >= start_at, Alert.occurred_at <= end_at
            )
        ).scalar_one()
        or 0
    )
    incident_count = int(
        db.execute(
            select(func.count(Incident.id)).where(
                Incident.window_start >= start_at,
                Incident.window_start <= end_at,
            )
        ).scalar_one()
        or 0
    )

    severity_distribution = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    rows = db.execute(
        select(Alert.severity, func.sum(Alert.repeat_count))
        .where(Alert.occurred_at >= start_at, Alert.occurred_at <= end_at)
        .group_by(Alert.severity)
    ).all()
    for severity, count in rows:
        if severity in severity_distribution:
            severity_distribution[severity] = int(count or 0)

    stats = compression_stats(db, start_at, end_at)

    evaluation = db.execute(
        select(Evaluation)
        .where(Evaluation.status == "completed")
        .order_by(Evaluation.created_at.desc())
    ).scalars().first()
    diagnosis_accuracy = 0.0
    evaluation_id = None
    if evaluation is not None:
        metrics = evaluation.metrics or {}
        diagnosis_accuracy = float(metrics.get("top1_accuracy", 0.0) or 0.0)
        evaluation_id = evaluation.id

    return {
        "window": {"start": start_at, "end": end_at},
        "alert_count": alert_count,
        "incident_count": incident_count,
        "compression_ratio": round(alert_count / incident_count, 2)
        if incident_count
        else 0.0,
        "severity_distribution": severity_distribution,
        "topology_hit_rate": stats["topology_hit_rate"],
        "diagnosis_accuracy": diagnosis_accuracy,
        "evaluation_id": evaluation_id,
        "generated_at": utcnow(),
    }

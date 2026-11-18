"""降噪聚合查询与统计路由。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.time import as_utc, utcnow
from app.db import get_db
from app.dedup.service import (
    ack_cluster,
    cluster_members,
    cluster_to_dict,
    list_clusters,
    noise_stats,
    resolve_cluster,
)
from app.ingestion.service import alert_to_dict
from app.models import AlertCluster

router = APIRouter(prefix="/api/v1", tags=["noise-reduction"])


def _default_window(
    start: datetime | None, end: datetime | None
) -> tuple[datetime, datetime]:
    end_at = as_utc(end or utcnow())
    start_at = as_utc(start or (end_at - timedelta(hours=24)))
    return start_at, end_at


@router.get("/clusters")
def get_clusters(
    host: str | None = None,
    service: str | None = None,
    status: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    items, total = list_clusters(
        db,
        host=host,
        service=service,
        status=status,
        start=start,
        end=end,
        page=page,
        page_size=page_size,
    )
    return {
        "items": [cluster_to_dict(item) for item in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/clusters/{cluster_id}")
def get_cluster(cluster_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    cluster = db.get(AlertCluster, cluster_id)
    if cluster is None:
        raise AppError("聚合告警不存在", code="not_found", status_code=404)
    members = cluster_members(db, cluster_id)
    data = cluster_to_dict(cluster, member_ids=[m.id for m in members])
    data["members"] = [alert_to_dict(m) for m in members]
    return data


@router.post("/clusters/{cluster_id}/ack")
def ack(cluster_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    cluster = ack_cluster(db, cluster_id)
    if cluster is None:
        raise AppError("聚合告警不存在", code="not_found", status_code=404)
    db.commit()
    return cluster_to_dict(cluster)


@router.post("/clusters/{cluster_id}/resolve")
def resolve(cluster_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    cluster = resolve_cluster(db, cluster_id)
    if cluster is None:
        raise AppError("聚合告警不存在", code="not_found", status_code=404)
    db.commit()
    return cluster_to_dict(cluster)


@router.get("/stats/noise")
def get_noise_stats(
    start: datetime | None = None,
    end: datetime | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    start_at, end_at = _default_window(start, end)
    return noise_stats(db, start_at, end_at)

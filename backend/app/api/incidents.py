"""故障事件查询与状态操作路由。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db import get_db
from app.models import Diagnosis, Incident
from app.topology.service import incident_to_dict, list_incidents

router = APIRouter(prefix="/api/v1", tags=["incidents"])


@router.get("/incidents")
def get_incidents(
    status: str | None = None,
    severity: str | None = None,
    batch_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    items, total = list_incidents(
        db,
        status=status,
        severity=severity,
        batch_id=batch_id,
        start=start,
        end=end,
        page=page,
        page_size=page_size,
    )
    latest: dict[int, Diagnosis] = {}
    if items:
        diagnoses = db.execute(
            select(Diagnosis)
            .where(Diagnosis.incident_id.in_([item.id for item in items]))
            .order_by(Diagnosis.version.desc())
        ).scalars().all()
        for diagnosis in diagnoses:
            latest.setdefault(diagnosis.incident_id, diagnosis)

    payload = []
    for item in items:
        data = incident_to_dict(item)
        diagnosis = latest.get(item.id)
        data["diagnosis_status"] = diagnosis.status if diagnosis else "none"
        data["diagnosis_id"] = diagnosis.id if diagnosis else None
        payload.append(data)
    return {
        "items": payload,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/incidents/{incident_id}")
def get_incident(
    incident_id: int, db: Session = Depends(get_db)
) -> dict[str, Any]:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise AppError("故障事件不存在", code="not_found", status_code=404)
    return incident_to_dict(incident, db=db, detail=True)


def _transition(db: Session, incident_id: int, target: str) -> Incident:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise AppError("故障事件不存在", code="not_found", status_code=404)
    if target == "acked" and incident.status == "open":
        incident.status = "acked"
    elif target == "resolved" and incident.status != "resolved":
        incident.status = "resolved"
    db.commit()
    return incident


@router.post("/incidents/{incident_id}/ack")
def ack_incident(
    incident_id: int, db: Session = Depends(get_db)
) -> dict[str, Any]:
    incident = _transition(db, incident_id, "acked")
    return incident_to_dict(incident)


@router.post("/incidents/{incident_id}/resolve")
def resolve_incident(
    incident_id: int, db: Session = Depends(get_db)
) -> dict[str, Any]:
    incident = _transition(db, incident_id, "resolved")
    return incident_to_dict(incident)

"""诊断 API 路由：触发诊断、诊断历史/详情、快速决策模型检测。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db import get_db
from app.diagnosis.decision import score_incident
from app.diagnosis.service import (
    list_diagnoses,
    serialize_diagnosis,
    start_diagnosis,
)
from app.models import Incident

router = APIRouter(prefix="/api/v1", tags=["diagnosis"])


def _incident_or_404(db: Session, incident_id: int) -> Incident:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise AppError("事件不存在", code="not_found", status_code=404)
    return incident


@router.post("/incidents/{incident_id}/diagnose")
def diagnose_incident(
    incident_id: int,
    rerun: bool = Query(False),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    diagnosis = start_diagnosis(db, incident_id, rerun=rerun)
    return {
        "diagnosis_id": diagnosis.id,
        "incident_id": diagnosis.incident_id,
        "status": diagnosis.status,
    }


@router.get("/incidents/{incident_id}/diagnoses")
def get_incident_diagnoses(
    incident_id: int, db: Session = Depends(get_db)
) -> dict[str, Any]:
    _incident_or_404(db, incident_id)
    items = list_diagnoses(db, incident_id)
    return {"items": [serialize_diagnosis(item) for item in items], "total": len(items)}


@router.get("/diagnoses/{diagnosis_id}")
def get_diagnosis(diagnosis_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.models import Diagnosis

    diagnosis = db.get(Diagnosis, diagnosis_id)
    if diagnosis is None:
        raise AppError("诊断记录不存在", code="not_found", status_code=404)
    return serialize_diagnosis(diagnosis)


@router.get("/incidents/{incident_id}/fast-diagnosis")
def fast_diagnosis(incident_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    incident = _incident_or_404(db, incident_id)
    result = score_incident(db, incident)
    return {
        "incident_id": incident.id,
        "model_version": result["model_version"],
        "candidates": result["candidates"],
    }

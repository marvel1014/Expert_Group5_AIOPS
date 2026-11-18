"""场景库、回放、评估 API 路由。"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.evaluation.scenarios import (
    get_scenario,
    list_scenarios,
    scenario_to_dict,
)
from app.evaluation.service import (
    evaluation_cases,
    get_evaluation,
    list_evaluations,
    replay_scenario,
    run_evaluation,
    serialize_evaluation,
)

router = APIRouter(prefix="/api/v1", tags=["evaluation"])


class ReplayRequest(BaseModel):
    compression: float | None = Field(default=None, gt=0, le=1000)
    seed: int = 42


class EvaluationRequest(BaseModel):
    scenario_ids: list[str] | None = None
    seed: int = 42


def _replay_to_dict(batch: Any) -> dict[str, Any]:
    return {
        "id": batch.id,
        "scenario_id": batch.scenario_id,
        "status": batch.status,
        "compression": batch.compression,
        "seed": batch.seed,
        "stats": batch.stats or {},
        "started_at": batch.started_at,
        "finished_at": batch.finished_at,
        "error": batch.error,
    }


@router.get("/scenarios")
def get_scenarios(db: Session = Depends(get_db)) -> dict[str, Any]:
    items = list_scenarios(db)
    return {
        "items": [scenario_to_dict(item) for item in items],
        "total": len(items),
    }


@router.get("/scenarios/{scenario_id}")
def get_scenario_detail(
    scenario_id: str, db: Session = Depends(get_db)
) -> dict[str, Any]:
    scenario = get_scenario(db, scenario_id)
    return scenario_to_dict(scenario, include_definition=True)


@router.post("/scenarios/{scenario_id}/replay")
def replay(
    scenario_id: str,
    payload: ReplayRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    body = payload or ReplayRequest()
    batch = replay_scenario(
        db, scenario_id, compression=body.compression, seed=body.seed
    )
    return _replay_to_dict(batch)


@router.post("/evaluations")
def create_evaluation(
    payload: EvaluationRequest | None = Body(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    body = payload or EvaluationRequest()
    evaluation = run_evaluation(db, scenario_ids=body.scenario_ids, seed=body.seed)
    return {"id": evaluation.id, "status": evaluation.status}


@router.get("/evaluations")
def get_evaluations(db: Session = Depends(get_db)) -> dict[str, Any]:
    items = list_evaluations(db)
    return {
        "items": [serialize_evaluation(item) for item in items],
        "total": len(items),
    }


@router.get("/evaluations/{evaluation_id}")
def get_evaluation_detail(
    evaluation_id: str, db: Session = Depends(get_db)
) -> dict[str, Any]:
    evaluation = get_evaluation(db, evaluation_id)
    return serialize_evaluation(
        evaluation, cases=evaluation_cases(db, evaluation.id)
    )


@router.get("/evaluations/{evaluation_id}/export")
def export_evaluation(
    evaluation_id: str, db: Session = Depends(get_db)
) -> Response:
    evaluation = get_evaluation(db, evaluation_id)
    payload = serialize_evaluation(
        evaluation, cases=evaluation_cases(db, evaluation.id)
    )
    content = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    filename = f"evaluation-{evaluation.id}.json"
    return Response(
        content=content,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

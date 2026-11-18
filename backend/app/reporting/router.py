"""报告 API 路由：生成、查询与 Markdown/JSON 导出。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Report
from app.reporting.service import (
    export_report,
    generate_report,
    get_report,
    serialize_report,
)

router = APIRouter(prefix="/api/v1", tags=["reports"])


@router.post("/incidents/{incident_id}/reports", status_code=201)
def create_report(
    incident_id: int,
    payload: dict[str, Any] | None = Body(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    diagnosis_id = None
    if payload:
        value = payload.get("diagnosis_id")
        if value is not None:
            try:
                diagnosis_id = int(value)
            except (TypeError, ValueError):
                from app.core.errors import AppError

                raise AppError(
                    "diagnosis_id 必须是整数",
                    code="validation_error",
                    status_code=422,
                )
    report = generate_report(db, incident_id, diagnosis_id=diagnosis_id)
    return serialize_report(report)


@router.get("/reports/{report_id}")
def get_report_detail(report_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    report: Report = get_report(db, report_id)
    return serialize_report(report)


@router.get("/reports/{report_id}/export")
def export_report_file(
    report_id: int,
    format: str = Query("markdown"),
    db: Session = Depends(get_db),
) -> Response:
    content, media_type, filename = export_report(db, report_id, format)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

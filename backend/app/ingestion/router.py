"""告警与日志接入 HTTP 路由。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, Query
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.db import get_db
from app.ingestion.schemas import AlertIn, LogIn
from app.ingestion.service import (
    alert_to_dict,
    ingest_alert,
    ingest_alerts,
    ingest_logs,
    list_alerts,
    list_logs,
    log_to_dict,
)
from app.topology.service import correlate_pending_alerts

router = APIRouter(prefix="/api/v1", tags=["ingestion"])


def _check_token(token: str | None) -> None:
    if settings.webhook_token and token != settings.webhook_token:
        raise AppError(
            "Webhook 令牌校验失败",
            code="unauthorized",
            status_code=401,
        )


def _as_batch(payload: Any, key: str) -> list[dict[str, Any]] | None:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get(key), list):
        return payload[key]
    return None


@router.post("/alerts/webhook", status_code=202)
def receive_alerts(
    payload: Any = Body(...),
    x_webhook_token: str | None = Header(default=None, alias="X-Webhook-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _check_token(x_webhook_token)
    if payload is None or not isinstance(payload, (dict, list)):
        raise AppError("请求体必须是告警对象或告警数组", status_code=422)

    batch = _as_batch(payload, "alerts")
    if batch is not None:
        summary = ingest_alerts(db, batch)
        correlate_pending_alerts(db, summary["alert_ids"])
        return summary

    try:
        data = AlertIn.model_validate(payload)
    except Exception as exc:
        raise AppError(
            "告警字段校验失败",
            code="validation_error",
            status_code=422,
            details=str(exc),
        ) from exc
    alert, duplicate = ingest_alert(db, data)
    db.commit()
    correlate_pending_alerts(db, [alert.id])
    return {
        "accepted": 1,
        "duplicates": 1 if duplicate else 0,
        "alert_id": alert.id,
        "duplicate": duplicate,
        "alert": alert_to_dict(alert),
    }


@router.get("/alerts")
def get_alerts(
    host: str | None = None,
    service: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    batch_id: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    items, total = list_alerts(
        db,
        host=host,
        service=service,
        severity=severity,
        status=status,
        batch_id=batch_id,
        start=start,
        end=end,
        page=page,
        page_size=page_size,
    )
    return {
        "items": [alert_to_dict(a) for a in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/alerts/{alert_id}")
def get_alert(alert_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.models import Alert

    alert = db.get(Alert, alert_id)
    if alert is None:
        raise AppError("告警不存在", code="not_found", status_code=404)
    return alert_to_dict(alert)


@router.post("/logs", status_code=202)
def receive_logs(
    payload: Any = Body(...),
    x_webhook_token: str | None = Header(default=None, alias="X-Webhook-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _check_token(x_webhook_token)
    if payload is None or not isinstance(payload, (dict, list)):
        raise AppError("请求体必须是日志对象或日志数组", status_code=422)
    batch = _as_batch(payload, "logs")
    if batch is not None:
        return ingest_logs(db, batch)
    try:
        data = LogIn.model_validate(payload)
    except Exception as exc:
        raise AppError(
            "日志字段校验失败",
            code="validation_error",
            status_code=422,
            details=str(exc),
        ) from exc
    summary = ingest_logs(db, [data.model_dump()])
    return summary


@router.get("/logs")
def get_logs(
    host: str | None = None,
    service: str | None = None,
    trace_id: str | None = None,
    level: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    items = list_logs(
        db,
        host=host,
        service=service,
        trace_id=trace_id,
        level=level,
        start=start,
        end=end,
        limit=limit,
    )
    return {"items": [log_to_dict(item) for item in items], "count": len(items)}

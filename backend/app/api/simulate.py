"""模拟故障注入路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db import get_db
from app.simulation import inject_burst, inject_scenario

router = APIRouter(prefix="/api/v1", tags=["simulation"])


@router.post("/simulate")
def simulate(payload: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    scenario_id = payload.get("scenario_id")
    if scenario_id:
        try:
            return inject_scenario(
                db,
                str(scenario_id),
                compression=payload.get("compression"),
                seed=int(payload.get("seed", 42)),
            )
        except AppError:
            raise
        except Exception as exc:  # 场景回放依赖评估模块
            raise AppError(
                f"场景回放失败: {exc}",
                code="simulation_failed",
                status_code=400,
            ) from exc

    mode = payload.get("mode", "burst")
    if mode != "burst":
        raise AppError(
            "未指定 scenario_id 时 mode 只支持 burst",
            code="validation_error",
            status_code=422,
        )
    count = int(payload.get("count", 30))
    return inject_burst(
        db,
        host=str(payload.get("host", "order-host-01")),
        service=payload.get("service", "order-service"),
        count=count,
        rule=str(payload.get("rule", "CPU usage high")),
        severity=str(payload.get("severity", "high")),
        spread_seconds=int(payload.get("spread_seconds", 60)),
    )

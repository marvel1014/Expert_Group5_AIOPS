"""模拟故障注入：突发告警与场景回放（场景回放委托给评估模块）。"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utcnow
from app.ingestion.schemas import AlertIn, LogIn
from app.ingestion.service import ingest_alert, ingest_logs
from app.models import IncidentAlert
from app.topology.service import correlate_pending_alerts

logger = logging.getLogger(__name__)

STACK_TEMPLATES = {
    "db_connection": (
        "TooManyConnectionsError: remaining connection slots are reserved\n"
        '  File "/app/order_service/db.py", line 88, in get_conn\n'
        "    conn = pool.acquire(timeout=5)\n"
        "sqlalchemy.exc.TimeoutError: QueuePool limit of size 20 overflow 10 reached"
    ),
    "network": (
        "ConnectionResetError: [Errno 104] Connection reset by peer\n"
        '  File "/app/gateway/proxy.py", line 61, in forward\n'
        "    upstream = await client.get(url)\n"
        "httpx.ConnectTimeout: timed out"
    ),
    "disk": (
        "OSError: [Errno 28] No space left on device\n"
        '  File "/app/payment_service/wal.py", line 42, in append\n'
        "    f.write(payload)"
    ),
    "cpu": (
        "RuntimeError: event loop is overloaded\n"
        '  File "/app/order_service/worker.py", line 130, in tick\n'
        "    await self.process_batch(batch)"
    ),
    "crash": (
        "SystemExit: process exited with code 137 (OOMKilled)\n"
        '  File "/app/user_service/__main__.py", line 12, in <module>\n'
        "    main()"
    ),
    "config": (
        "ValueError: invalid argument: connect_timeout must be > 0\n"
        '  File "/app/payment_service/config.py", line 27, in validate\n'
        "    raise ValueError(msg)"
    ),
}


def _stack_for(rule: str) -> str:
    lower = rule.lower()
    if any(k in lower for k in ("connection", "database", "mysql", "pool", "deadlock")):
        return STACK_TEMPLATES["db_connection"]
    if any(k in lower for k in ("network", "timeout", "packet", "dns", "reset")):
        return STACK_TEMPLATES["network"]
    if any(k in lower for k in ("disk", "space", "filesystem", "inode")):
        return STACK_TEMPLATES["disk"]
    if any(k in lower for k in ("cpu", "load")):
        return STACK_TEMPLATES["cpu"]
    if any(k in lower for k in ("crash", "exited", "process", "restart")):
        return STACK_TEMPLATES["crash"]
    return STACK_TEMPLATES["config"]


def inject_burst(
    db: Session,
    *,
    host: str = "order-host-01",
    service: str | None = "order-service",
    count: int = 30,
    rule: str = "CPU usage high",
    severity: str = "high",
    spread_seconds: int = 60,
) -> dict[str, Any]:
    """注入同一指纹的突发告警与带堆栈日志，用于演示降噪与关联。"""
    count = max(1, min(int(count), 500))
    batch_id = f"burst-{uuid.uuid4().hex[:10]}"
    now = utcnow()
    alert_ids: list[int] = []
    for index in range(count):
        occurred = now - timedelta(seconds=spread_seconds) + timedelta(
            seconds=spread_seconds * index / count
        )
        # 动态参数会被规则归一化抹掉，因此落在同一指纹/时间桶内
        data = AlertIn(
            source="simulate",
            host=host,
            service=service,
            rule=f"{rule} {85 + index % 15}%",
            severity=severity,
            message=f"模拟故障注入 #{index + 1}",
            labels={"mode": "burst", "batch": batch_id},
            occurred_at=occurred,
            batch_id=batch_id,
        )
        alert, _ = ingest_alert(db, data)
        alert_ids.append(alert.id)
    db.commit()

    log_items = []
    for index, alert_id in enumerate(alert_ids[:: max(1, count // 6)][:6]):
        log_items.append(
            {
                "ts": now - timedelta(seconds=max(0, spread_seconds - index * 5)),
                "host": host,
                "service": service,
                "level": "ERROR",
                "message": f"{rule} 触发异常",
                "stack": _stack_for(rule),
                "trace_id": f"trace-{batch_id}-{index}",
                "alert_id": alert_id,
                "batch_id": batch_id,
            }
        )
    log_count = ingest_logs(db, log_items)["accepted"] if log_items else 0

    correlate_pending_alerts(db, alert_ids)
    incident_ids = sorted(
        set(
            db.execute(
                select(IncidentAlert.incident_id).where(
                    IncidentAlert.alert_id.in_(alert_ids)
                )
            ).scalars().all()
        )
    )
    return {
        "batch_id": batch_id,
        "alert_count": len(alert_ids),
        "log_count": log_count,
        "incident_ids": incident_ids,
    }


def inject_scenario(
    db: Session,
    scenario_id: str,
    *,
    compression: float | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    """委托评估模块按场景回放；返回统一的注入响应。"""
    from app.evaluation.service import replay_scenario

    batch = replay_scenario(
        db, scenario_id, compression=compression, seed=seed
    )
    stats = batch.stats or {}
    return {
        "batch_id": batch.id,
        "scenario_id": batch.scenario_id,
        "alert_count": int(stats.get("alert_count", 0)),
        "log_count": int(stats.get("log_count", 0)),
        "incident_ids": stats.get("incident_ids", []),
        "status": batch.status,
    }

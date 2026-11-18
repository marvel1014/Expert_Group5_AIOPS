"""后台维护任务：聚合告警/事件冷却解决与待关联告警补偿。"""

from __future__ import annotations

import asyncio
import logging

from app.db import session_scope
from app.dedup.service import sweep_clusters
from app.topology.service import correlate_pending_alerts, sweep_incidents

logger = logging.getLogger(__name__)


async def maintenance_loop(interval_seconds: int = 30) -> None:
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            with session_scope() as db:
                resolved_clusters = sweep_clusters(db)
                resolved_incidents = sweep_incidents(db)
            with session_scope() as db:
                correlate_pending_alerts(db)
            if resolved_clusters or resolved_incidents:
                logger.info(
                    "maintenance: resolved %s clusters, %s incidents",
                    resolved_clusters,
                    resolved_incidents,
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - 守护任务不应中断主服务
            logger.exception("maintenance loop error")

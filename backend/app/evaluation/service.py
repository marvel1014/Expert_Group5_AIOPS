"""场景回放与诊断准确率评估。"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.time import utcnow
from app.diagnosis.decision import score_incident
from app.diagnosis.service import run_diagnosis_sync
from app.evaluation.scenarios import get_scenario, list_scenarios
from app.ingestion.service import ingest_alerts, ingest_logs
from app.models import (
    Evaluation,
    EvaluationCase,
    Incident,
    ReplayBatch,
)
from app.topology.service import correlate_pending_alerts

logger = logging.getLogger(__name__)

# 回放使用"最近一小时内的虚拟时间槽"：事件落在看板默认时间窗内可见，
# 同时按关联时间窗错开批次，避免不同回放批次之间、回放与在线事件之间互相合并。
_VIRTUAL_INITIAL_OFFSET_SECONDS = 60
_VIRTUAL_SLOT_SPACING_SECONDS = 300
_VIRTUAL_SLOT_COUNT = 10


def _pick_virtual_base(db: Session, span_seconds: float) -> Any:
    """为回放挑选一个不与尚未解决的故障事件冲突的虚拟起始时间。

    - 首个槽位为 utcnow() - 60s，保证回放事件出现在看板最近时间窗；
    - 后续槽位按 300 秒回退，避开拓扑关联窗口（默认 180 秒）与去重窗口；
    - 槽位全部被占用时，回收冲突的历史"回放事件"（batch_id 非空）后回到最近槽位，
      不修改在线事件，保证回放与评估仍然相互隔离。
    """
    now = utcnow()
    correlation = timedelta(seconds=settings.correlation_window_seconds)
    span = timedelta(seconds=max(0.0, float(span_seconds)))

    def conflicting_incident_filter(base: Any) -> Any:
        return (
            Incident.status.in_(["open", "acked"]),
            Incident.window_end >= base - correlation,
            Incident.window_start <= base + span + correlation,
        )

    for index in range(_VIRTUAL_SLOT_COUNT):
        base = now - timedelta(
            seconds=_VIRTUAL_INITIAL_OFFSET_SECONDS
            + index * _VIRTUAL_SLOT_SPACING_SECONDS
        )
        conflict = db.execute(
            select(func.count())
            .select_from(Incident)
            .where(*conflicting_incident_filter(base))
        ).scalar_one()
        if not conflict:
            return base

    # 槽位耗尽：只回收历史回放事件，避免影响在线事件
    base = now - timedelta(seconds=_VIRTUAL_INITIAL_OFFSET_SECONDS)
    db.execute(
        update(Incident)
        .where(
            *conflicting_incident_filter(base),
            Incident.batch_id.is_not(None),
        )
        .values(status="resolved")
    )
    db.commit()
    return base


def replay_scenario(
    db: Session,
    scenario_id: str,
    compression: float | None = None,
    seed: int = 42,
) -> ReplayBatch:
    """按时间压缩倍率用虚拟时钟注入告警与日志，再完成拓扑关联，返回批次记录。"""
    scenario = get_scenario(db, scenario_id)
    definition = scenario.definition or {}
    try:
        ratio = float(compression if compression is not None else definition.get("compression") or 10.0)
    except (TypeError, ValueError):
        ratio = 10.0
    if ratio <= 0:
        raise AppError("compression 必须大于 0", code="validation_error", status_code=422)

    batch_id = f"batch-{uuid.uuid4().hex[:12]}"
    batch = ReplayBatch(
        id=batch_id,
        scenario_id=scenario.id,
        seed=int(seed),
        compression=ratio,
        status="running",
        stats={},
    )
    db.add(batch)
    db.commit()

    max_offset = max(
        (float(item.get("offset", 0)) for item in definition.get("alerts") or []),
        default=0.0,
    )
    base_time = _pick_virtual_base(db, max_offset / ratio)
    try:
        alert_items: list[dict[str, Any]] = []
        for alert in definition.get("alerts") or []:
            occurred = base_time + timedelta(seconds=float(alert.get("offset", 0)) / ratio)
            alert_items.append(
                {
                    # source 携带批次号，保证不同回放批次不会在去重窗口内互相吞并
                    "source": f"replay:{batch_id}",
                    "host": alert["host"],
                    "service": alert.get("service"),
                    "rule": alert["rule"],
                    "severity": alert.get("severity", "medium"),
                    "message": alert.get("message", ""),
                    "labels": {
                        **(alert.get("labels") or {}),
                        "scenario_id": scenario.id,
                        "replay": True,
                    },
                    "occurred_at": occurred,
                    "batch_id": batch_id,
                }
            )
        log_items: list[dict[str, Any]] = []
        for log in definition.get("logs") or []:
            ts = base_time + timedelta(seconds=float(log.get("offset", 0)) / ratio)
            log_items.append(
                {
                    "ts": ts,
                    "host": log["host"],
                    "service": log.get("service"),
                    "level": log.get("level", "ERROR"),
                    "message": log.get("message", ""),
                    "stack": log.get("stack", ""),
                    "trace_id": log.get("trace_id"),
                    "batch_id": batch_id,
                }
            )

        alert_summary = ingest_alerts(db, alert_items) if alert_items else {"accepted": 0}
        log_summary = ingest_logs(db, log_items) if log_items else {"accepted": 0}
        correlate_pending_alerts(db)

        incident_ids = list(
            db.execute(
                select(Incident.id)
                .where(Incident.batch_id == batch_id)
                .order_by(Incident.id.asc())
            ).scalars().all()
        )
        batch.status = "completed"
        batch.finished_at = utcnow()
        batch.stats = {
            "alert_count": int(alert_summary.get("accepted", 0)),
            "log_count": int(log_summary.get("accepted", 0)),
            "incident_ids": incident_ids,
            "incident_count": len(incident_ids),
            "compression": ratio,
            "seed": int(seed),
            "virtual_base": base_time.isoformat(),
        }
        db.commit()
    except Exception as exc:
        logger.exception("场景回放失败: %s", scenario_id)
        batch.status = "failed"
        batch.error = f"{type(exc).__name__}: {exc}"
        batch.finished_at = utcnow()
        db.commit()
        raise

    return batch


def _pick_incident(incidents: list[Incident], expected_node: str) -> Incident | None:
    """场景可能拆成多个事件（干扰告警），优先选择包含期望根因节点的事件。"""
    for incident in incidents:
        if expected_node and expected_node in (incident.nodes or []):
            return incident
    if not incidents:
        return None
    return max(incidents, key=lambda item: (item.alert_count or 0, item.id))


def _accuracy(hits: int, total: int) -> float:
    return round(hits / total, 4) if total else 0.0


def serialize_evaluation(
    evaluation: Evaluation, cases: list[EvaluationCase] | None = None
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": evaluation.id,
        "status": evaluation.status,
        "config": evaluation.config or {},
        "metrics": evaluation.metrics or {},
        "created_at": evaluation.created_at,
        "finished_at": evaluation.finished_at,
        "error": evaluation.error,
    }
    if cases is not None:
        data["cases"] = [
            {
                "scenario_id": case.scenario_id,
                "expected": case.expected or {},
                "actual": case.actual or {},
                "top1_hit": case.top1_hit,
                "top3_hit": case.top3_hit,
                "node_hit": case.node_hit,
                "latency_ms": case.latency_ms,
                "error": case.error,
            }
            for case in cases
        ]
    return data


def evaluation_cases(db: Session, evaluation_id: str) -> list[EvaluationCase]:
    return list(
        db.execute(
            select(EvaluationCase)
            .where(EvaluationCase.evaluation_id == evaluation_id)
            .order_by(EvaluationCase.id.asc())
        ).scalars().all()
    )


def get_evaluation(db: Session, evaluation_id: str) -> Evaluation:
    evaluation = db.get(Evaluation, evaluation_id)
    if evaluation is None:
        raise AppError("评估记录不存在", code="not_found", status_code=404)
    return evaluation


def list_evaluations(db: Session, limit: int = 100) -> list[Evaluation]:
    return list(
        db.execute(
            select(Evaluation).order_by(Evaluation.created_at.desc()).limit(limit)
        ).scalars().all()
    )


def run_evaluation(
    db: Session,
    scenario_ids: list[str] | None = None,
    seed: int = 42,
    compression: float | None = None,
) -> Evaluation:
    """回放场景并对事件执行确定性诊断，比对期望值后持久化评估结果。

    评估模式强制使用 Mock 诊断（决策模型结果），保证相同场景集与种子可复现。
    """
    if scenario_ids:
        scenarios = [get_scenario(db, scenario_id) for scenario_id in scenario_ids]
    else:
        scenarios = list_scenarios(db)
    if not scenarios:
        raise AppError("场景库为空，无法评估", code="validation_error", status_code=422)

    evaluation = Evaluation(
        id=f"eval-{uuid.uuid4().hex[:12]}",
        status="running",
        config={
            "scenario_ids": [scenario.id for scenario in scenarios],
            "seed": int(seed),
            "llm_mode": "mock",
            "compression": compression,
        },
        metrics={},
    )
    db.add(evaluation)
    db.commit()

    cases: list[EvaluationCase] = []
    try:
        for scenario in scenarios:
            definition = scenario.definition or {}
            expected = scenario.expected or {}
            expected_category = str(expected.get("root_cause_category") or "")
            expected_node = str(expected.get("root_cause_node") or "")
            ratio = compression
            if ratio is None:
                try:
                    ratio = float(definition.get("compression") or 10.0)
                except (TypeError, ValueError):
                    ratio = 10.0

            try:
                batch = replay_scenario(db, scenario.id, compression=ratio, seed=seed)
                incident_ids = (batch.stats or {}).get("incident_ids") or []
                incidents = (
                    list(
                        db.execute(
                            select(Incident).where(Incident.id.in_(incident_ids))
                        ).scalars().all()
                    )
                    if incident_ids
                    else []
                )
                incident = _pick_incident(incidents, expected_node)
                if incident is None:
                    raise RuntimeError("回放未产生故障事件")

                diagnosis = run_diagnosis_sync(db, incident, mode="mock")
                decision = score_incident(db, incident)
                top3 = [item["category"] for item in decision.get("candidates") or []][:3]
                actual = {
                    "category": diagnosis.root_cause_category,
                    "node": diagnosis.root_cause_node,
                    "confidence": diagnosis.confidence,
                    "status": diagnosis.status,
                    "source": diagnosis.source,
                    "model": diagnosis.model,
                    "diagnosis_id": diagnosis.id,
                    "incident_id": incident.id,
                    "top3_categories": top3,
                }
                top1_hit = bool(
                    expected_category
                    and diagnosis.root_cause_category == expected_category
                )
                top3_hit = bool(expected_category and expected_category in top3)
                node_hit = bool(expected_node and diagnosis.root_cause_node == expected_node)
                raw_alerts = len(definition.get("alerts") or [])
                compression_ratio = round(
                    raw_alerts / max(1, len(incident_ids)), 2
                )
                case = EvaluationCase(
                    evaluation_id=evaluation.id,
                    scenario_id=scenario.id,
                    expected=expected,
                    actual={**actual, "compression_ratio": compression_ratio},
                    top1_hit=top1_hit,
                    top3_hit=top3_hit,
                    node_hit=node_hit,
                    latency_ms=int(diagnosis.latency_ms or 0),
                    error=None,
                )
            except Exception as exc:  # 单用例失败不中断整批评估
                logger.exception("评估用例失败: %s", scenario.id)
                case = EvaluationCase(
                    evaluation_id=evaluation.id,
                    scenario_id=scenario.id,
                    expected=expected,
                    actual={},
                    top1_hit=False,
                    top3_hit=False,
                    node_hit=False,
                    latency_ms=0,
                    error=f"{type(exc).__name__}: {exc}",
                )
            db.add(case)
            db.flush()
            cases.append(case)

        total = len(cases)
        metrics = {
            "total": total,
            "top1_accuracy": _accuracy(sum(1 for c in cases if c.top1_hit), total),
            "top3_accuracy": _accuracy(sum(1 for c in cases if c.top3_hit), total),
            "node_accuracy": _accuracy(sum(1 for c in cases if c.node_hit), total),
            "avg_latency_ms": int(
                round(sum(c.latency_ms for c in cases) / total, 0)
            ) if total else 0,
            "avg_compression_ratio": round(
                sum(
                    float((c.actual or {}).get("compression_ratio") or 0.0)
                    for c in cases
                )
                / total,
                2,
            ) if total else 0.0,
        }
        evaluation.metrics = metrics
        evaluation.status = "completed"
        evaluation.finished_at = utcnow()
        db.commit()
    except Exception as exc:
        evaluation.status = "failed"
        evaluation.error = f"{type(exc).__name__}: {exc}"
        evaluation.finished_at = utcnow()
        db.commit()
        raise
    return evaluation

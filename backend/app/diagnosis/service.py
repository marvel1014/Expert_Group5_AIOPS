"""诊断编排服务：证据选择、日志脱敏、LLM 调用、结构化校验、降级与状态机。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.time import as_utc
from app.db import session_scope
from app.dedup.service import severity_rank
from app.diagnosis.decision import load_decision_model, score_incident
from app.diagnosis.llm import (
    LLMError,
    LLMResponse,
    MockLLMClient,
    build_llm_client,
    parse_json_object,
)
from app.ingestion.service import alert_to_dict, log_to_dict
from app.models import Alert, Diagnosis, Incident, IncidentAlert, LogEntry
from app.topology.service import load_topology

logger = logging.getLogger(__name__)

PROMPT_FILE = Path(__file__).resolve().parent / "prompts" / "rca_v1.md"
_LOG_CONTEXT_SECONDS = 120
_LOG_SCAN_LIMIT = 300
_ERROR_LEVELS = {"ERROR", "FATAL", "CRITICAL", "PANIC"}
_GENERIC_ERROR_RE = re.compile(
    r"error|exception|fail|timeout|timed out|refused|unreachable|reset|panic|fatal",
    re.IGNORECASE,
)

# ------------------------------ 日志脱敏 ------------------------------

# 键值型敏感字段：password=xxx / token: xxx / api_key = xxx ...
_KV_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|pass|token|secret|api[_-]?key|access[_-]?key|"
    r"authorization|auth[_-]?token|session[_-]?id)\b(\s*[=:]\s*)([\"']?)([^\s\"',;}\]]+)"
)
_BEARER_RE = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")

PLACEHOLDER = "***REDACTED***"


def sanitize_text(text: str | None) -> str:
    """对日志文本执行敏感信息脱敏：密码/令牌/密钥/手机号/身份证/邮箱。"""
    if not text:
        return ""
    # Bearer 先处理，避免 Authorization 键规则把 "Bearer" 当成值而漏掉真实令牌
    result = _BEARER_RE.sub("Bearer " + PLACEHOLDER, text)
    result = _KV_SECRET_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{PLACEHOLDER}", result
    )
    result = _PHONE_RE.sub("***PHONE***", result)
    result = _ID_CARD_RE.sub("***ID***", result)
    result = _EMAIL_RE.sub("***EMAIL***", result)
    return result


def sanitize_log(entry: LogEntry | dict[str, Any]) -> dict[str, Any]:
    data = log_to_dict(entry) if isinstance(entry, LogEntry) else dict(entry)
    data["message"] = sanitize_text(data.get("message"))
    data["stack"] = sanitize_text(data.get("stack"))
    return data


# ------------------------------ Prompt ------------------------------


def _prompt_section(content: str, name: str) -> str:
    pattern = re.compile(rf"(?ms)^## {name}\s*\n(.*?)(?=^## |\Z)")
    match = pattern.search(content)
    return match.group(1).strip() if match else ""


def load_prompt() -> tuple[str, str, str]:
    """读取 Prompt 文件，返回 (prompt_version, system, user_template)。

    prompt_version 为内容 SHA-256 前 8 位，保证 Prompt 修改可追溯。
    """
    try:
        content = PROMPT_FILE.read_text(encoding="utf-8")
    except OSError:
        logger.exception("读取诊断 Prompt 失败，使用内置兜底 Prompt")
        content = "## system\n你是 SRE 根因分析专家，只输出 JSON。\n\n## user\n{{payload}}\n"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
    system = _prompt_section(content, "system") or "你是 SRE 根因分析专家，只输出 JSON。"
    user = _prompt_section(content, "user") or "{{payload}}"
    return f"rca_v1:{digest}", system, user


# ------------------------------ 证据选择 ------------------------------


def _alert_node(alert: Alert) -> str | None:
    topology = load_topology()
    resolved = topology.resolve(alert.service, alert.host)
    if resolved:
        return resolved
    fallback = (alert.service or alert.host or "").strip().lower()
    return f"unknown:{fallback}" if fallback else None


def _trim_stack(stack: str, max_lines: int = 14, max_chars: int = 1200) -> str:
    if not stack:
        return ""
    lines = stack.splitlines()
    compact: list[str] = []
    for line in lines:
        # 折叠连续重复的堆栈帧
        if compact and line.strip() and line.strip() == compact[-1].strip():
            continue
        compact.append(line.rstrip())
    if len(compact) > max_lines:
        head = compact[: max_lines - 4]
        tail = [line for line in compact[-4:] if line not in head]
        compact = head + tail
    text = "\n".join(compact)
    return text[:max_chars]


def _incident_alerts(db: Session, incident: Incident) -> list[Alert]:
    alert_ids = db.execute(
        select(IncidentAlert.alert_id).where(IncidentAlert.incident_id == incident.id)
    ).scalars().all()
    if not alert_ids:
        return []
    return list(
        db.execute(select(Alert).where(Alert.id.in_(alert_ids))).scalars().all()
    )


def _incident_logs(db: Session, incident: Incident, alerts: list[Alert]) -> list[LogEntry]:
    hosts = {a.host for a in alerts if a.host}
    services = {a.service for a in alerts if a.service}
    if not hosts and not services:
        return []
    from sqlalchemy import or_

    conditions = []
    if hosts:
        conditions.append(LogEntry.host.in_(hosts))
    if services:
        conditions.append(LogEntry.service.in_(services))
    window_start = as_utc(incident.window_start) - timedelta(seconds=_LOG_CONTEXT_SECONDS)
    window_end = as_utc(incident.window_end) + timedelta(seconds=_LOG_CONTEXT_SECONDS)
    return list(
        db.execute(
            select(LogEntry)
            .where(
                or_(*conditions),
                LogEntry.ts >= window_start,
                LogEntry.ts <= window_end,
            )
            .order_by(LogEntry.ts.asc())
            .limit(_LOG_SCAN_LIMIT)
        ).scalars().all()
    )


def _error_signatures() -> list[re.Pattern[str]]:
    """合并决策模型中的日志签名与通用错误关键词，用于日志证据筛选。"""
    model = load_decision_model()
    patterns = [
        "|".join(re.escape(pattern) for _compiled, pattern, _weight in rule.log_patterns)
        for rule in model.categories.values()
    ]
    combined = "|".join(p for p in patterns if p)
    result: list[re.Pattern[str]] = []
    if combined:
        result.append(re.compile(combined, re.IGNORECASE))
    result.append(_GENERIC_ERROR_RE)
    return result


def _redact_alert(alert: Alert) -> dict[str, Any]:
    data = alert_to_dict(alert)
    data["message"] = sanitize_text(data.get("message"))
    data["rule"] = sanitize_text(data.get("rule"))
    data["labels"] = data.get("labels") or {}
    data["occurred_at"] = as_utc(data["occurred_at"]).isoformat()
    data["first_seen"] = as_utc(data["first_seen"]).isoformat()
    data["last_seen"] = as_utc(data["last_seen"]).isoformat()
    return data


def build_evidence(db: Session, incident: Incident) -> dict[str, Any]:
    """选择受预算约束的核心告警与错误日志，返回脱敏后的证据集。"""
    max_alerts = max(1, settings.evidence_max_alerts)
    max_logs = max(1, settings.evidence_max_log_lines)
    max_chars = max(500, settings.evidence_max_chars)

    alerts = _incident_alerts(db, incident)
    root_nodes = [
        str(item.get("node"))
        for item in (incident.root_candidates or [])
        if item.get("node")
    ][:2]

    def alert_priority(alert: Alert) -> tuple[int, int, Any, int]:
        node = _alert_node(alert)
        core = 0 if node in root_nodes else 1
        return (core, -severity_rank(alert.severity), as_utc(alert.first_seen), alert.id)

    alerts.sort(key=alert_priority)

    alert_budget = max(300, max_chars // 2)
    used_chars = 0
    selected_alerts: list[Alert] = []
    for alert in alerts:
        if len(selected_alerts) >= max_alerts:
            break
        text = f"{alert.rule} {alert.message}"
        if selected_alerts and used_chars + len(text) > alert_budget:
            break
        selected_alerts.append(alert)
        used_chars += len(text)

    selected_alert_dicts = [_redact_alert(a) for a in selected_alerts]
    missing: list[str] = []
    if not selected_alerts:
        missing.append("事件缺少核心告警证据")

    # 日志：错误签名命中优先 -> 级别高优先 -> 时间早
    logs = _incident_logs(db, incident, alerts)
    signatures = _error_signatures()

    def log_priority(entry: LogEntry) -> tuple[int, int, Any, int]:
        text = f"{entry.message} {entry.stack}"
        matched = any(p.search(text) for p in signatures)
        level_boost = 1 if (entry.level or "").upper() in _ERROR_LEVELS else 0
        return (0 if matched else 1, -level_boost, as_utc(entry.ts), entry.id)

    error_logs = [
        entry
        for entry in logs
        if (entry.level or "").upper() in _ERROR_LEVELS
        or any(p.search(f"{entry.message} {entry.stack}") for p in signatures)
    ]
    candidate_logs = error_logs or logs
    candidate_logs.sort(key=log_priority)

    log_budget = max(200, max_chars - used_chars)
    selected_logs: list[LogEntry] = []
    log_chars = 0
    for entry in candidate_logs:
        if len(selected_logs) >= max_logs:
            break
        redacted = sanitize_log(entry)
        text = f"{redacted['message']} {redacted['stack']}"
        if selected_logs and log_chars + len(text) > log_budget:
            break
        selected_logs.append(entry)
        log_chars += len(text)

    selected_log_dicts = [sanitize_log(entry) for entry in selected_logs]
    for item in selected_log_dicts:
        item["ts"] = as_utc(item["ts"]).isoformat()
        item["stack"] = _trim_stack(item.get("stack") or "")
    if not selected_log_dicts:
        missing.append("根因节点缺少日志证据（未找到错误级日志或关联日志）")

    used_chars += log_chars
    return {
        "alerts": selected_alert_dicts,
        "logs": selected_log_dicts,
        "refs": {
            "alert_ids": [item["id"] for item in selected_alert_dicts],
            "log_ids": [item["id"] for item in selected_log_dicts],
        },
        "budget": {
            "max_alerts": max_alerts,
            "max_log_lines": max_logs,
            "max_chars": max_chars,
            "used_chars": used_chars,
        },
        "missing": missing,
    }


# ------------------------------ 结构化输出校验 ------------------------------


class DiagnosisOutput(BaseModel):
    """LLM 结构化输出 Schema。"""

    root_cause_category: str
    root_cause_node: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = Field(min_length=1)
    affected_nodes: list[str] = Field(default_factory=list)
    evidence_alert_ids: list[int] = Field(default_factory=list)
    evidence_log_ids: list[int] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    needs_review: bool = False


def validate_diagnosis_output(
    raw: dict[str, Any],
    *,
    allowed_categories: set[str],
    alert_ids: set[int],
    log_ids: set[int],
    incident_nodes: set[str],
) -> tuple[DiagnosisOutput | None, list[str]]:
    """校验根因类别枚举、置信度范围、节点归属与证据引用存在性。"""
    try:
        output = DiagnosisOutput.model_validate(raw)
    except ValidationError as exc:
        errors = [
            f"{'.'.join(str(p) for p in item['loc'])}: {item['msg']}"
            for item in exc.errors()
        ]
        return None, errors

    errors: list[str] = []
    if allowed_categories and output.root_cause_category not in allowed_categories:
        errors.append(
            f"root_cause_category 不在候选类别中: {output.root_cause_category}"
        )
    if output.root_cause_node and incident_nodes and output.root_cause_node not in incident_nodes:
        errors.append(f"root_cause_node 不在事件节点中: {output.root_cause_node}")
    unknown_alerts = sorted(set(output.evidence_alert_ids) - alert_ids)
    if unknown_alerts:
        errors.append(f"引用了不存在的告警证据 id: {unknown_alerts}")
    unknown_logs = sorted(set(output.evidence_log_ids) - log_ids)
    if unknown_logs:
        errors.append(f"引用了不存在的日志证据 id: {unknown_logs}")
    if (alert_ids or log_ids) and not (output.evidence_alert_ids or output.evidence_log_ids):
        errors.append("必须至少引用一条已提供的证据（evidence_alert_ids 或 evidence_log_ids）")
    if errors:
        return None, errors
    return output, []


# ------------------------------ Mock 结论 ------------------------------


def _build_mock_payload(
    decision: dict[str, Any],
    incident: Incident,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """由决策模型与证据生成确定性 Mock 结论（与真实模式共享 Schema）。"""
    top = (decision.get("candidates") or [{}])[0]
    node = None
    for item in incident.root_candidates or []:
        if item.get("node"):
            node = str(item["node"])
            break
    if node is None and incident.nodes:
        node = str(incident.nodes[0])
    features = top.get("features") or []
    reasoning = (
        f"决策模型判定为 {top.get('label') or top.get('category')}"
        f"（得分 {top.get('score')}）。命中特征：{'、'.join(str(f) for f in features[:5]) or '无'}。"
        f"事件涉及节点 {', '.join(incident.nodes or [])}，最高严重级别 {incident.severity}。"
        "（Mock 模式：未调用外部大模型，结论来自确定性规则模型）"
    )
    refs = evidence.get("refs") or {}
    return {
        "root_cause_category": top.get("category"),
        "root_cause_node": node,
        "confidence": round(float(top.get("confidence") or 0.0), 4),
        "reasoning": reasoning,
        "affected_nodes": list(incident.nodes or []),
        "evidence_alert_ids": list(refs.get("alert_ids") or [])[:5],
        "evidence_log_ids": list(refs.get("log_ids") or [])[:5],
        "missing_evidence": list(evidence.get("missing") or []),
        "needs_review": False,
    }


# ------------------------------ 状态机与执行 ------------------------------


def _degraded_payload(
    decision: dict[str, Any],
    incident: Incident,
    evidence: dict[str, Any],
    *,
    cap: float,
) -> dict[str, Any]:
    top = (decision.get("candidates") or [{}])[0]
    node = None
    for item in incident.root_candidates or []:
        if item.get("node"):
            node = str(item["node"])
            break
    if node is None and incident.nodes:
        node = str(incident.nodes[0])
    confidence = min(float(top.get("confidence") or 0.0), cap)
    features = top.get("features") or []
    reasoning = (
        f"大模型不可用，使用决策模型降级结论：{top.get('label') or top.get('category')}"
        f"（得分 {top.get('score')}，置信度上限 {cap}）。"
        f"命中特征：{'、'.join(str(f) for f in features[:5]) or '无'}。"
        f"事件涉及节点 {', '.join(incident.nodes or [])}，最高严重级别 {incident.severity}。"
        "建议值班人员结合现场信息人工复核。"
    )
    refs = evidence.get("refs") or {}
    return {
        "root_cause_category": top.get("category"),
        "root_cause_node": node,
        "confidence": round(confidence, 4),
        "reasoning": reasoning,
        "affected_nodes": list(incident.nodes or []),
        "evidence_alert_ids": list(refs.get("alert_ids") or [])[:5],
        "evidence_log_ids": list(refs.get("log_ids") or [])[:5],
        "missing_evidence": list(evidence.get("missing") or []),
        "needs_review": True,
    }


def _build_user_prompt(
    template: str,
    incident: Incident,
    evidence: dict[str, Any],
    decision: dict[str, Any],
    prompt_version: str,
) -> str:
    payload = {
        "prompt_version": prompt_version,
        "incident": {
            "id": incident.id,
            "title": incident.title,
            "severity": incident.severity,
            "status": incident.status,
            "window_start": as_utc(incident.window_start).isoformat(),
            "window_end": as_utc(incident.window_end).isoformat(),
            "nodes": list(incident.nodes or []),
            "root_candidates": list(incident.root_candidates or [])[:5],
            "alert_count": incident.alert_count,
            "topology_linked": incident.topology_linked,
        },
        "decision_model": {
            "model_version": decision.get("model_version"),
            "candidates": decision.get("candidates") or [],
        },
        "evidence": {
            "alerts": evidence.get("alerts") or [],
            "logs": evidence.get("logs") or [],
            "refs": evidence.get("refs") or {},
            "missing": evidence.get("missing") or [],
        },
        "output_schema": {
            "root_cause_category": "候选类别之一",
            "root_cause_node": "事件节点或 null",
            "confidence": "0~1",
            "reasoning": "中文推理说明",
            "affected_nodes": ["节点"],
            "evidence_alert_ids": ["已提供告警 id"],
            "evidence_log_ids": ["已提供日志 id"],
            "missing_evidence": ["缺失证据"],
            "needs_review": False,
        },
        "constraints": {
            "evidence_alert_ids": sorted(evidence.get("refs", {}).get("alert_ids") or []),
            "evidence_log_ids": sorted(evidence.get("refs", {}).get("log_ids") or []),
            "allowed_categories": [c.get("category") for c in decision.get("candidates") or []],
            "incident_nodes": list(incident.nodes or []),
        },
    }
    return template.replace(
        "{{payload}}", json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    )


class _Degrade(Exception):
    """内部降级信号。"""

    def __init__(
        self,
        reason: str,
        *,
        detail: str = "",
        validation_errors: list[str] | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail
        self.validation_errors = validation_errors or []


def _extract_output(
    raw: dict[str, Any],
    *,
    allowed_names: set[str],
    evidence: dict[str, Any],
    incident: Incident,
) -> DiagnosisOutput:
    refs = evidence.get("refs") or {}
    output, errors = validate_diagnosis_output(
        raw,
        allowed_categories=allowed_names,
        alert_ids=set(refs.get("alert_ids") or []),
        log_ids=set(refs.get("log_ids") or []),
        incident_nodes={str(n) for n in (incident.nodes or [])},
    )
    if output is None:
        raise _Degrade("llm_output_invalid", detail="；".join(errors), validation_errors=errors)
    return output


def _call_llm(
    client: Any,
    system: str,
    user_content: str,
    *,
    allowed_names: set[str],
    evidence: dict[str, Any],
    incident: Incident,
) -> tuple[DiagnosisOutput, LLMResponse]:
    """调用模型并做一次修复重试；失败抛出 _Degrade。"""
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]
    try:
        response = client.complete(messages)
    except LLMError as exc:
        raise _Degrade(f"llm_{exc.kind}", detail=str(exc)) from exc

    try:
        raw = parse_json_object(response.content)
        return _extract_output(
            raw,
            allowed_names=allowed_names,
            evidence=evidence,
            incident=incident,
        ), response
    except (LLMError, _Degrade) as first_error:
        errors = (
            first_error.validation_errors
            if isinstance(first_error, _Degrade)
            else [str(first_error)]
        )
        logger.info("诊断输出校验失败，回传模型修复重试一次: %s", errors)
        repair = (
            "你上一次的输出未通过结构校验，错误如下：\n"
            + "\n".join(f"- {item}" for item in errors)
            + "\n请重新只输出一个合法 JSON 对象，不要输出任何其他文本。上一次输出：\n"
            + response.content[:800]
        )
        retry_messages = messages + [
            {"role": "assistant", "content": response.content},
            {"role": "user", "content": repair},
        ]
        try:
            second = client.complete(retry_messages)
        except LLMError as exc:
            raise _Degrade(
                "llm_output_invalid",
                detail=f"{exc}; 首次校验错误: {errors}",
                validation_errors=errors,
            ) from exc
        try:
            raw = parse_json_object(second.content)
            output = _extract_output(
                raw,
                allowed_names=allowed_names,
                evidence=evidence,
                incident=incident,
            )
        except (LLMError, _Degrade) as second_error:
            second_errors = (
                second_error.validation_errors
                if isinstance(second_error, _Degrade)
                else [str(second_error)]
            )
            raise _Degrade(
                "llm_output_invalid",
                detail="；".join(errors + second_errors),
                validation_errors=errors + second_errors,
            ) from second_error
        # 修复成功仍记录曾发生的校验失败，便于审计
        response.token_usage = second.token_usage
        response.model = second.model or response.model
        response.attempts += second.attempts
        return output, response


def _resolve_allowed_names(decision: dict[str, Any]) -> set[str]:
    return {str(item["category"]) for item in decision.get("candidates") or [] if item.get("category")}


def _apply_result(
    diagnosis: Diagnosis,
    output: dict[str, Any],
    *,
    evidence: dict[str, Any],
    status: str,
    source: str,
    reasoning: str,
    confidence: float,
    model: str,
    prompt_version: str,
    latency_ms: int,
    token_usage: dict[str, Any],
    config_snapshot: dict[str, Any],
    error: str | None,
) -> None:
    missing = list(dict.fromkeys(list(output.get("missing_evidence") or []) + list(evidence.get("missing") or [])))
    needs_review = bool(output.get("needs_review")) or bool(missing) or confidence < settings.review_confidence_threshold
    if source == "degraded":
        needs_review = True
    diagnosis.status = status
    diagnosis.source = source
    diagnosis.root_cause_category = output.get("root_cause_category")
    diagnosis.root_cause_node = output.get("root_cause_node")
    diagnosis.confidence = round(float(confidence), 4)
    diagnosis.reasoning = reasoning
    diagnosis.missing_evidence = missing
    diagnosis.needs_review = needs_review
    diagnosis.model = model
    diagnosis.prompt_version = prompt_version
    diagnosis.latency_ms = int(latency_ms)
    diagnosis.token_usage = token_usage or {}
    diagnosis.config_snapshot = config_snapshot
    diagnosis.error = error
    evidence["cited"] = {
        "alert_ids": list(output.get("evidence_alert_ids") or []),
        "log_ids": list(output.get("evidence_log_ids") or []),
    }
    diagnosis.evidence = evidence


def _get_or_create_diagnosis(db: Session, incident: Incident, diagnosis: Diagnosis | None) -> Diagnosis:
    if diagnosis is not None:
        return diagnosis
    existing = db.execute(
        select(Diagnosis)
        .where(Diagnosis.incident_id == incident.id, Diagnosis.status.in_(["pending", "running"]))
        .order_by(Diagnosis.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    latest = get_latest_diagnosis(db, incident.id)
    diagnosis = Diagnosis(
        incident_id=incident.id,
        version=(latest.version + 1) if latest else 1,
        status="pending",
        source="pending",
    )
    db.add(diagnosis)
    db.flush()
    return diagnosis


def run_diagnosis_sync(
    db: Session,
    incident: Incident | int,
    mode: str | None = None,
    *,
    client: Any = None,
    diagnosis: Diagnosis | None = None,
) -> Diagnosis:
    """同步执行一次诊断（评估 / CLI / 后台任务复用同一实现）。"""
    if isinstance(incident, int):
        loaded = db.get(Incident, incident)
        if loaded is None:
            raise AppError("事件不存在", code="not_found", status_code=404)
        incident = loaded
    diagnosis = _get_or_create_diagnosis(db, incident, diagnosis)

    requested_mode = mode or settings.effective_llm_mode
    diagnosis.status = "running"
    diagnosis.source = "pending"
    diagnosis.error = None
    db.flush()
    db.commit()

    started = time.monotonic()
    prompt_version, system, user_template = load_prompt()
    decision: dict[str, Any] = {}
    evidence: dict[str, Any] = {}
    config_snapshot: dict[str, Any] = {
        "llm_mode": requested_mode,
        "llm_configured": settings.llm_configured,
        "llm_model": settings.llm_model if requested_mode != "mock" else "mock-rule-fusion",
        "evidence_budget": {
            "max_alerts": settings.evidence_max_alerts,
            "max_log_lines": settings.evidence_max_log_lines,
            "max_chars": settings.evidence_max_chars,
        },
        "prompt_version": prompt_version,
        "degraded_confidence_cap": settings.degraded_confidence_cap,
        "review_confidence_threshold": settings.review_confidence_threshold,
    }

    try:
        decision = score_incident(db, incident)
        evidence = build_evidence(db, incident)
        config_snapshot["decision_model_version"] = decision.get("model_version")
        config_snapshot["decision_candidates"] = decision.get("candidates") or []
        allowed_names = _resolve_allowed_names(decision)
        user_content = _build_user_prompt(
            user_template, incident, evidence, decision, prompt_version
        )

        if requested_mode == "mock" or client is not None and isinstance(client, MockLLMClient):
            mock_payload = _build_mock_payload(decision, incident, evidence)
            llm_client = client or MockLLMClient(mock_payload)
            try:
                response = llm_client.complete(
                    [{"role": "system", "content": system},
                     {"role": "user", "content": user_content}]
                )
            except LLMError as exc:
                raise _Degrade(f"mock_error", detail=str(exc)) from exc
            try:
                raw = parse_json_object(response.content)
                output = _extract_output(
                    raw,
                    allowed_names=allowed_names,
                    evidence=evidence,
                    incident=incident,
                )
            except (LLMError, _Degrade) as exc:
                raise exc
            latency_ms = int((time.monotonic() - started) * 1000)
            payload = output.model_dump()
            _apply_result(
                diagnosis,
                payload,
                evidence=evidence,
                status="succeeded",
                source="mock",
                reasoning=output.reasoning,
                confidence=output.confidence,
                model=response.model or "mock-rule-fusion",
                prompt_version=prompt_version,
                latency_ms=latency_ms,
                token_usage=response.token_usage,
                config_snapshot=config_snapshot,
                error=None,
            )
            db.commit()
            return diagnosis

        if requested_mode == "llm" and not settings.llm_configured:
            raise _Degrade(
                "llm_not_configured",
                detail="未配置 LLM_API_KEY / LLM_BASE_URL，降级为决策模型结论",
            )
        llm_client = client or build_llm_client("llm")
        output, response = _call_llm(
            llm_client,
            system,
            user_content,
            allowed_names=allowed_names,
            evidence=evidence,
            incident=incident,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        payload = output.model_dump()
        _apply_result(
            diagnosis,
            payload,
            evidence=evidence,
            status="succeeded",
            source="llm",
            reasoning=output.reasoning,
            confidence=output.confidence,
            model=response.model or settings.llm_model,
            prompt_version=prompt_version,
            latency_ms=latency_ms,
            token_usage=response.token_usage,
            config_snapshot=config_snapshot,
            error=None,
        )
        db.commit()
        return diagnosis

    except _Degrade as exc:
        # 降级：决策模型 + 模板化推理，置信度不超过配置上限
        cap = float(settings.degraded_confidence_cap)
        if not decision:
            try:
                decision = score_incident(db, incident)
            except Exception:  # pragma: no cover - 决策模型本身异常时保底
                decision = {"model_version": "unknown", "candidates": []}
        if not evidence:
            try:
                evidence = build_evidence(db, incident)
            except Exception:  # pragma: no cover
                evidence = {"alerts": [], "logs": [], "refs": {}, "budget": {}, "missing": []}
        payload = _degraded_payload(decision, incident, evidence, cap=cap)
        config_snapshot["degraded_reason"] = exc.reason
        config_snapshot["degraded_detail"] = exc.detail
        if exc.validation_errors:
            config_snapshot["validation_errors"] = exc.validation_errors
        latency_ms = int((time.monotonic() - started) * 1000)
        _apply_result(
            diagnosis,
            payload,
            evidence=evidence,
            status="degraded",
            source="degraded",
            reasoning=payload["reasoning"],
            confidence=payload["confidence"],
            model=f"degraded-{decision.get('model_version', 'rule')}",
            prompt_version=prompt_version,
            latency_ms=latency_ms,
            token_usage={},
            config_snapshot=config_snapshot,
            error=f"{exc.reason}: {exc.detail}" if exc.detail else exc.reason,
        )
        db.commit()
        logger.warning(
            "incident %s 诊断降级: %s %s", incident.id, exc.reason, exc.detail
        )
        return diagnosis
    except Exception as exc:  # pragma: no cover - 兜底失败状态
        logger.exception("incident %s 诊断执行失败", incident.id)
        latency_ms = int((time.monotonic() - started) * 1000)
        diagnosis.status = "failed"
        diagnosis.source = "failed"
        diagnosis.error = f"{type(exc).__name__}: {exc}"
        diagnosis.latency_ms = latency_ms
        diagnosis.prompt_version = prompt_version
        diagnosis.config_snapshot = config_snapshot
        db.commit()
        return diagnosis


def get_latest_diagnosis(db: Session, incident_id: int) -> Diagnosis | None:
    return db.execute(
        select(Diagnosis)
        .where(Diagnosis.incident_id == incident_id)
        .order_by(Diagnosis.version.desc())
        .limit(1)
    ).scalar_one_or_none()


def list_diagnoses(db: Session, incident_id: int) -> list[Diagnosis]:
    return list(
        db.execute(
            select(Diagnosis)
            .where(Diagnosis.incident_id == incident_id)
            .order_by(Diagnosis.version.desc())
        ).scalars().all()
    )


def _execute_diagnosis(diagnosis_id: int) -> None:
    """后台线程执行入口：独立会话，异常只落日志不影响主请求。"""
    try:
        with session_scope() as db:
            diagnosis = db.get(Diagnosis, diagnosis_id)
            if diagnosis is None or diagnosis.status not in ("pending", "running"):
                return
            incident = db.get(Incident, diagnosis.incident_id)
            if incident is None:
                diagnosis.status = "failed"
                diagnosis.error = "事件不存在"
                return
            run_diagnosis_sync(db, incident, diagnosis=diagnosis)
    except Exception:  # pragma: no cover - 后台任务守护
        logger.exception("后台诊断任务失败: diagnosis_id=%s", diagnosis_id)


async def _execute_diagnosis_async(diagnosis_id: int) -> None:
    # LLM 为同步 HTTP 调用，放到线程池避免阻塞事件循环
    await asyncio.to_thread(_execute_diagnosis, diagnosis_id)


def start_diagnosis(db: Session, incident_id: int, rerun: bool = False) -> Diagnosis:
    """创建诊断记录并异步执行；已有执行中/已完成的诊断时按幂等语义返回。"""
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise AppError("事件不存在", code="not_found", status_code=404)

    latest = get_latest_diagnosis(db, incident_id)
    if latest is not None and not rerun:
        if latest.status in ("pending", "running"):
            return latest
        if latest.status in ("succeeded", "degraded"):
            # 默认查询复用最近一次结论，避免重复调用模型
            return latest

    diagnosis = Diagnosis(
        incident_id=incident_id,
        version=(latest.version + 1) if latest else 1,
        status="pending",
        source="pending",
    )
    db.add(diagnosis)
    db.commit()
    db.refresh(diagnosis)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 同步上下文（CLI / 单测）：直接执行，避免丢弃任务
        run_diagnosis_sync(db, incident, diagnosis=diagnosis)
        return diagnosis
    loop.create_task(_execute_diagnosis_async(diagnosis.id))
    return diagnosis


def recover_stale_diagnoses(db: Session) -> int:
    """进程重启后把残留 running 诊断回收为 failed，返回回收数量。"""
    result = db.execute(
        update(Diagnosis)
        .where(Diagnosis.status == "running")
        .values(status="failed", error="进程重启，回收残留 running 诊断")
    )
    db.commit()
    return int(result.rowcount or 0)


def serialize_diagnosis(diagnosis: Diagnosis) -> dict[str, Any]:
    return {
        "id": diagnosis.id,
        "incident_id": diagnosis.incident_id,
        "version": diagnosis.version,
        "status": diagnosis.status,
        "source": diagnosis.source,
        "root_cause_category": diagnosis.root_cause_category,
        "root_cause_node": diagnosis.root_cause_node,
        "confidence": diagnosis.confidence,
        "reasoning": diagnosis.reasoning,
        "evidence": diagnosis.evidence or {},
        "missing_evidence": diagnosis.missing_evidence or [],
        "needs_review": diagnosis.needs_review,
        "model": diagnosis.model,
        "prompt_version": diagnosis.prompt_version,
        "latency_ms": diagnosis.latency_ms,
        "token_usage": diagnosis.token_usage or {},
        "config_snapshot": diagnosis.config_snapshot or {},
        "created_at": diagnosis.created_at,
        "error": diagnosis.error,
    }

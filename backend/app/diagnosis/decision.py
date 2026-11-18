"""可解释决策模型：载入配置、提取事件特征、规则打分。

设计要点（见 design.md D5）：

- 纯内存规则打分，不依赖大模型，同一输入严格确定性；
- 每个故障类别维护关键词 / 规则族 / 日志错误签名 / 节点角色权重；
- 输出 Top-N 候选、归一化得分、置信度与命中特征，便于向评审解释得分来源；
- 单事件计算为毫秒级，满足 < 500ms 的演示性能要求。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.time import as_utc
from app.dedup.service import severity_rank
from app.models import Alert, Incident, IncidentAlert, LogEntry
from app.topology.service import load_topology

_CATEGORY_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_LOG_CONTEXT_SECONDS = 120
_LOG_SCAN_LIMIT = 300


@dataclass(frozen=True)
class CategoryRule:
    """单个故障类别的可解释权重配置。"""

    name: str
    label: str
    node_kinds: dict[str, float]
    rule_keywords: tuple[tuple[str, float], ...]
    rule_families: dict[str, float]
    log_patterns: tuple[tuple[re.Pattern[str], str, float], ...]


@dataclass(frozen=True)
class DecisionModel:
    version: str
    description: str
    categories: dict[str, CategoryRule]
    no_match_prior: float
    severity_boost: float
    max_score: float
    top_n: int

    @property
    def category_names(self) -> tuple[str, ...]:
        return tuple(sorted(self.categories))


def _as_weight(value: Any, where: str, errors: list[str]) -> float:
    try:
        weight = float(value)
    except (TypeError, ValueError):
        errors.append(f"{where} 权重必须是数字: {value!r}")
        return 0.0
    if weight < 0 or weight > 1:
        errors.append(f"{where} 权重必须在 [0, 1] 区间: {weight}")
    return weight


def validate_decision_model(data: Any) -> DecisionModel:
    """校验配置结构；非法类别或空权重配置抛出 AppError。"""
    errors: list[str] = []
    if not isinstance(data, dict):
        raise AppError(
            "决策模型配置必须是 YAML 映射",
            code="invalid_decision_model",
            details=["根节点类型不是 dict"],
        )

    version = data.get("version")
    if not isinstance(version, str) or not version.strip():
        errors.append("缺少字符串字段 version")

    defaults = data.get("defaults") or {}
    if not isinstance(defaults, dict):
        errors.append("defaults 必须是映射")
        defaults = {}
    no_match_prior = _as_weight(defaults.get("no_match_prior", 0.01), "defaults.no_match_prior", errors)
    severity_boost = _as_weight(defaults.get("severity_boost", 0.05), "defaults.severity_boost", errors)
    max_score = _as_weight(defaults.get("max_score", 1.0), "defaults.max_score", errors)
    if max_score <= 0:
        errors.append("defaults.max_score 必须大于 0")
    try:
        top_n = int(defaults.get("top_n", 3))
    except (TypeError, ValueError):
        top_n = 3
        errors.append("defaults.top_n 必须是整数")
    if top_n < 1 or top_n > 10:
        errors.append("defaults.top_n 必须在 1~10 之间")

    raw_categories = data.get("categories")
    if not isinstance(raw_categories, dict) or not raw_categories:
        errors.append("categories 必须是非空映射")

    categories: dict[str, CategoryRule] = {}
    for name, raw in (raw_categories or {}).items():
        name = str(name)
        where = f"categories.{name}"
        if not _CATEGORY_NAME_RE.match(name):
            errors.append(f"{where} 类别名非法（须小写字母/数字/下划线）")
            continue
        if not isinstance(raw, dict):
            errors.append(f"{where} 必须是映射")
            continue

        node_kinds: dict[str, float] = {}
        raw_node_kinds = raw.get("node_kinds") or {}
        if not isinstance(raw_node_kinds, dict):
            errors.append(f"{where}.node_kinds 必须是映射")
        else:
            for kind, weight in raw_node_kinds.items():
                node_kinds[str(kind)] = _as_weight(weight, f"{where}.node_kinds.{kind}", errors)

        rule_keywords: list[tuple[str, float]] = []
        raw_keywords = raw.get("rule_keywords") or []
        if not isinstance(raw_keywords, list):
            errors.append(f"{where}.rule_keywords 必须是列表")
        else:
            for index, item in enumerate(raw_keywords):
                if not isinstance(item, dict):
                    errors.append(f"{where}.rule_keywords[{index}] 必须是 {{pattern, weight}}")
                    continue
                pattern = str(item.get("pattern") or "").strip().lower()
                if not pattern:
                    errors.append(f"{where}.rule_keywords[{index}] pattern 不能为空")
                    continue
                rule_keywords.append(
                    (pattern, _as_weight(item.get("weight", 0), f"{where}.rule_keywords[{index}]", errors))
                )

        rule_families: dict[str, float] = {}
        raw_families = raw.get("rule_families") or {}
        if not isinstance(raw_families, dict):
            errors.append(f"{where}.rule_families 必须是映射")
        else:
            for family, weight in raw_families.items():
                rule_families[str(family)] = _as_weight(weight, f"{where}.rule_families.{family}", errors)

        log_patterns: list[tuple[re.Pattern[str], str, float]] = []
        raw_patterns = raw.get("log_patterns") or []
        if not isinstance(raw_patterns, list):
            errors.append(f"{where}.log_patterns 必须是列表")
        else:
            for index, item in enumerate(raw_patterns):
                if not isinstance(item, dict):
                    errors.append(f"{where}.log_patterns[{index}] 必须是 {{pattern, weight}}")
                    continue
                pattern = str(item.get("pattern") or "")
                if not pattern:
                    errors.append(f"{where}.log_patterns[{index}] pattern 不能为空")
                    continue
                try:
                    compiled = re.compile(pattern, re.IGNORECASE)
                except re.error as exc:
                    errors.append(f"{where}.log_patterns[{index}] 正则非法: {exc}")
                    continue
                log_patterns.append(
                    (
                        compiled,
                        pattern,
                        _as_weight(item.get("weight", 0), f"{where}.log_patterns[{index}]", errors),
                    )
                )

        if not (node_kinds or rule_keywords or rule_families or log_patterns):
            errors.append(f"{where} 权重配置为空：至少配置一类特征")

        label = str(raw.get("label") or name)
        categories[name] = CategoryRule(
            name=name,
            label=label,
            node_kinds=node_kinds,
            rule_keywords=tuple(rule_keywords),
            rule_families=rule_families,
            log_patterns=tuple(log_patterns),
        )

    if errors:
        raise AppError(
            "决策模型配置校验失败",
            code="invalid_decision_model",
            details=errors,
        )
    assert isinstance(version, str)
    return DecisionModel(
        version=version.strip(),
        description=str(data.get("description") or ""),
        categories=categories,
        no_match_prior=no_match_prior,
        severity_boost=severity_boost,
        max_score=max_score,
        top_n=top_n,
    )


_model_cache: dict[str, tuple[float, DecisionModel]] = {}


def load_decision_model(path: str | None = None, *, force: bool = False) -> DecisionModel:
    """载入决策模型配置；默认读取 settings.decision_model_file 并按 mtime 缓存。"""
    file_path = settings.path(path or settings.decision_model_file)
    key = str(file_path)
    if not file_path.exists():
        if key in _model_cache and not force:
            return _model_cache[key][1]
        raise AppError(
            f"决策模型配置不存在: {file_path}",
            code="invalid_decision_model",
            details=[str(file_path)],
        )
    mtime = file_path.stat().st_mtime
    cached = _model_cache.get(key)
    if cached is not None and not force and cached[0] == mtime:
        return cached[1]
    try:
        data = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise AppError(
            "决策模型配置 YAML 解析失败",
            code="invalid_decision_model",
            details=[str(exc)],
        ) from exc
    model = validate_decision_model(data)
    _model_cache[key] = (mtime, model)
    return model


def model_version() -> str:
    return load_decision_model().version


def _node_for_alert(alert: Alert) -> str | None:
    topology = load_topology()
    resolved = topology.resolve(alert.service, alert.host)
    if resolved:
        return resolved
    fallback = (alert.service or alert.host or "").strip().lower()
    return f"unknown:{fallback}" if fallback else None


def incident_alerts(db: Session, incident: Incident) -> list[Alert]:
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
    conditions = []
    if hosts:
        conditions.append(LogEntry.host.in_(hosts))
    if services:
        conditions.append(LogEntry.service.in_(services))
    if not conditions:
        return []
    window_start = as_utc(incident.window_start) - timedelta(seconds=_LOG_CONTEXT_SECONDS)
    window_end = as_utc(incident.window_end) + timedelta(seconds=_LOG_CONTEXT_SECONDS)
    from sqlalchemy import or_

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


def extract_features(db: Session, incident: Incident) -> dict[str, Any]:
    """从事件中提取规则打分所需的告警、日志、节点特征（确定性顺序）。"""
    alerts = incident_alerts(db, incident)
    alerts.sort(
        key=lambda a: (
            -severity_rank(a.severity),
            as_utc(a.first_seen),
            a.id,
        )
    )
    logs = _incident_logs(db, incident, alerts)
    topology = load_topology()

    node_order = [str(node) for node in (incident.nodes or []) if node]
    if not node_order:
        resolved = {_node_for_alert(alert) for alert in alerts}
        node_order = sorted(node for node in resolved if node)

    # 根因候选节点优先，其次事件涉及节点；用于节点角色特征
    candidate_nodes: list[str] = []
    for item in incident.root_candidates or []:
        node = str(item.get("node") or "")
        if node and node not in candidate_nodes:
            candidate_nodes.append(node)
    for node in node_order:
        if node not in candidate_nodes:
            candidate_nodes.append(node)
    ranked_nodes = candidate_nodes[:2]

    alert_text = " ".join(
        f"{a.rule} {a.message} {a.rule_family}".lower() for a in alerts
    )
    families = {a.rule_family for a in alerts if a.rule_family}
    return {
        "alerts": alerts,
        "logs": logs,
        "nodes": node_order,
        "ranked_nodes": ranked_nodes,
        "node_kinds": {
            node: (topology.nodes.get(node, {}).get("kind") or "")
            for node in candidate_nodes
        },
        "alert_text": alert_text,
        "families": families,
        "severity": incident.severity,
        "alert_count": incident.alert_count or len(alerts),
    }


def _score_category(rule: CategoryRule, features: dict[str, Any]) -> tuple[float, list[str]]:
    score = 0.0
    matched: list[str] = []
    alert_text: str = features["alert_text"]

    for pattern, weight in rule.rule_keywords:
        if pattern and pattern in alert_text:
            score += weight
            matched.append(f"rule:{pattern}")

    for family, weight in rule.rule_families.items():
        if family in features["families"]:
            score += weight
            matched.append(f"family:{family}")

    log_texts = [
        f"{entry.message} {entry.stack}" for entry in features["logs"] if entry
    ]
    for compiled, pattern, weight in rule.log_patterns:
        if any(compiled.search(text or "") for text in log_texts):
            score += weight
            matched.append(f"log:{pattern}")

    node_kinds = features["node_kinds"]
    for node in features["ranked_nodes"]:
        kind = node_kinds.get(node) or ""
        if kind and kind in rule.node_kinds:
            score += rule.node_kinds[kind]
            matched.append(f"node_kind:{kind}@{node}")
            break

    return score, matched


def score_incident(db: Session, incident: Incident) -> dict[str, Any]:
    """对事件打分并返回 Top-N 候选根因，附带命中特征与模型版本。"""
    model = load_decision_model()
    features = extract_features(db, incident)

    severity_boosted = severity_rank(features["severity"]) >= 4 and bool(features["alerts"])
    candidates: list[dict[str, Any]] = []
    for name in sorted(model.categories):
        rule = model.categories[name]
        raw_score, matched = _score_category(rule, features)
        if severity_boosted:
            raw_score += model.severity_boost
            matched.append(f"severity:{features['severity']}")
        score = round(min(model.max_score, raw_score), 4)
        if score <= 0:
            score = round(model.no_match_prior, 4)
            matched = matched or ["no_feature_match"]
        candidates.append(
            {
                "category": name,
                "label": rule.label,
                "score": score,
                "confidence": score,
                "features": matched,
            }
        )

    candidates.sort(key=lambda item: (-item["score"], item["category"]))
    top = candidates[: model.top_n]
    return {
        "incident_id": incident.id,
        "model_version": model.version,
        "candidates": top,
    }

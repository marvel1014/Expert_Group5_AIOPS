"""故障场景库：YAML 定义校验、载入入库、查询与序列化。

场景文件是唯一事实来源，启动时同步入库（幂等 upsert），评估与回放都从数据库读取。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.models import Scenario
from app.topology.service import load_topology

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_REQUIRED_TOP = ("id", "name", "category", "topology", "alerts", "expected")


class ScenarioValidationError(AppError):
    def __init__(self, message: str, details: list[str], *, source_file: str = "") -> None:
        items = list(details)
        if source_file:
            items = [f"{source_file}: {item}" for item in items]
        super().__init__(
            message,
            code="invalid_scenario",
            status_code=422,
            details=items,
        )


def _resolve_node(service: str | None, host: str | None, aliases: dict[str, str]) -> str | None:
    for value in (service, host):
        if value and str(value).strip().lower() in aliases:
            return aliases[str(value).strip().lower()]
    return None


def _normalize_topology(
    raw: Any, errors: list[str], where: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    aliases: dict[str, str] = {}
    if not isinstance(raw, dict):
        errors.append(f"{where} 必须是映射，包含 nodes 与 edges")
        return nodes, edges, aliases
    raw_nodes = raw.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        errors.append(f"{where}.nodes 必须是非空列表")
        raw_nodes = []
    for index, node in enumerate(raw_nodes):
        node_where = f"{where}.nodes[{index}]"
        if not isinstance(node, dict) or not node.get("id"):
            errors.append(f"{node_where} 缺少 id")
            continue
        node_id = str(node["id"]).strip()
        if node_id in aliases:
            errors.append(f"{node_where} 节点 id 重复: {node_id}")
            continue
        kind = str(node.get("kind") or "service")
        name = str(node.get("name") or node_id)
        normalized = {"id": node_id, "kind": kind, "name": name}
        nodes.append(normalized)
        aliases[node_id.lower()] = node_id
        aliases[name.lower()] = node_id

    raw_edges = raw.get("edges") or []
    if not isinstance(raw_edges, list):
        errors.append(f"{where}.edges 必须是列表")
        raw_edges = []
    for index, edge in enumerate(raw_edges):
        edge_where = f"{where}.edges[{index}]"
        if not isinstance(edge, dict):
            errors.append(f"{edge_where} 必须是 {{upstream, downstream}} 映射")
            continue
        upstream = str(edge.get("upstream") or "")
        downstream = str(edge.get("downstream") or "")
        if upstream not in {node["id"] for node in nodes}:
            errors.append(f"{edge_where} upstream 节点不存在: {upstream}")
        if downstream not in {node["id"] for node in nodes}:
            errors.append(f"{edge_where} downstream 节点不存在: {downstream}")
        try:
            weight = float(edge.get("weight", 1.0))
        except (TypeError, ValueError):
            errors.append(f"{edge_where} weight 必须是数字")
            weight = 1.0
        edges.append({"upstream": upstream, "downstream": downstream, "weight": weight})
    return nodes, edges, aliases


def _check_global_topology(nodes: list[dict[str, Any]], errors: list[str]) -> None:
    """场景引用的拓扑节点必须存在于全局拓扑中，否则回放时无法正确关联。"""
    try:
        global_topology = load_topology()
    except AppError as exc:  # pragma: no cover - 全局拓扑非法时由拓扑模块负责报错
        errors.append(f"全局拓扑不可用: {exc.message}")
        return
    if not global_topology.nodes:
        return
    for node in nodes:
        if node["id"] not in global_topology.nodes:
            errors.append(f"节点 {node['id']} 不在全局拓扑 config/topology.yaml 中")


def validate_scenario(data: Any, *, source_file: str = "") -> dict[str, Any]:
    """校验并规范化单个场景定义；错误收集后一次性抛出，指明字段位置与原因。"""
    errors: list[str] = []
    if not isinstance(data, dict):
        raise ScenarioValidationError(
            "场景定义必须是 YAML 映射", ["根节点不是映射"], source_file=source_file
        )
    for field in _REQUIRED_TOP:
        if data.get(field) in (None, "", [], {}):
            errors.append(f"缺少必填字段 {field}")

    scenario_id = str(data.get("id") or "").strip()
    if scenario_id and not _ID_RE.match(scenario_id):
        errors.append(f"id 非法（须小写字母/数字/-/_）: {scenario_id}")
    name = str(data.get("name") or "").strip()
    category = str(data.get("category") or "").strip()
    description = str(data.get("description") or "")
    try:
        compression = float(data.get("compression", 10))
    except (TypeError, ValueError):
        errors.append("compression 必须是数字")
        compression = 10.0
    if compression <= 0:
        errors.append("compression 必须大于 0")

    nodes, edges, aliases = _normalize_topology(data.get("topology"), errors, "topology")
    _check_global_topology(nodes, errors)

    raw_alerts = data.get("alerts")
    alerts: list[dict[str, Any]] = []
    if not isinstance(raw_alerts, list) or not raw_alerts:
        errors.append("alerts 必须是非空列表")
        raw_alerts = []
    for index, raw in enumerate(raw_alerts):
        where = f"alerts[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{where} 必须是映射")
            continue
        host = str(raw.get("host") or "").strip()
        service = raw.get("service")
        rule = str(raw.get("rule") or "").strip()
        if not host:
            errors.append(f"{where}.host 不能为空")
        if not rule:
            errors.append(f"{where}.rule 不能为空")
        try:
            offset = float(raw.get("offset", 0))
        except (TypeError, ValueError):
            errors.append(f"{where}.offset 必须是数字（秒）")
            offset = 0.0
        if offset < 0:
            errors.append(f"{where}.offset 不能为负数")
        node = _resolve_node(str(service) if service else None, host, aliases)
        if node is None:
            errors.append(
                f"{where} 引用了拓扑中不存在的节点: host={host!r} service={service!r}"
            )
        alerts.append(
            {
                "offset": offset,
                "host": host,
                "service": str(service) if service else None,
                "rule": rule,
                "severity": str(raw.get("severity") or "medium"),
                "message": str(raw.get("message") or ""),
                "labels": dict(raw.get("labels") or {}),
                "node": node,
            }
        )

    raw_logs = data.get("logs") or []
    logs: list[dict[str, Any]] = []
    if not isinstance(raw_logs, list):
        errors.append("logs 必须是列表")
        raw_logs = []
    for index, raw in enumerate(raw_logs):
        where = f"logs[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{where} 必须是映射")
            continue
        host = str(raw.get("host") or "").strip()
        if not host:
            errors.append(f"{where}.host 不能为空")
        try:
            offset = float(raw.get("offset", 0))
        except (TypeError, ValueError):
            errors.append(f"{where}.offset 必须是数字（秒）")
            offset = 0.0
        logs.append(
            {
                "offset": offset,
                "host": host,
                "service": str(raw["service"]) if raw.get("service") else None,
                "level": str(raw.get("level") or "ERROR"),
                "message": str(raw.get("message") or ""),
                "stack": str(raw.get("stack") or ""),
                "trace_id": str(raw["trace_id"]) if raw.get("trace_id") else None,
            }
        )

    raw_expected = data.get("expected")
    expected: dict[str, Any] = {}
    if not isinstance(raw_expected, dict):
        errors.append("expected 必须是映射")
    else:
        expected_category = str(raw_expected.get("root_cause_category") or "").strip()
        expected_node = str(raw_expected.get("root_cause_node") or "").strip()
        affected_raw = raw_expected.get("affected_nodes") or []
        if not expected_category:
            errors.append("expected.root_cause_category 不能为空")
        if not expected_node:
            errors.append("expected.root_cause_node 不能为空")
        elif expected_node not in {node["id"] for node in nodes}:
            errors.append(
                f"expected.root_cause_node 不在拓扑节点中: {expected_node}"
            )
        if not isinstance(affected_raw, list) or not affected_raw:
            errors.append("expected.affected_nodes 必须是非空列表")
            affected: list[str] = []
        else:
            affected = [str(item) for item in affected_raw]
            for item in affected:
                if item not in {node["id"] for node in nodes}:
                    errors.append(f"expected.affected_nodes 引用了不存在的节点: {item}")
        expected = {
            "root_cause_category": expected_category,
            "root_cause_node": expected_node,
            "affected_nodes": affected,
        }

    if errors:
        raise ScenarioValidationError(
            "场景定义校验失败",
            errors,
            source_file=source_file,
        )

    return {
        "id": scenario_id,
        "name": name,
        "category": category,
        "description": description,
        "compression": compression,
        "definition": {
            "compression": compression,
            "topology": {"nodes": nodes, "edges": edges},
            "alerts": alerts,
            "logs": logs,
        },
        "expected": expected,
        "source_file": source_file,
    }


def load_scenario_file(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    try:
        data = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ScenarioValidationError(
            "场景 YAML 解析失败",
            [str(exc)],
            source_file=file_path.name,
        ) from exc
    return validate_scenario(data, source_file=file_path.name)


def load_scenarios_from_dir(
    db: Session, directory: str | Path | None = None
) -> int:
    """载入场景目录下全部 YAML 并幂等 upsert；返回载入数量。"""
    base = settings.path(directory) if directory else settings.path(settings.scenarios_dir)
    if not base.exists() or not base.is_dir():
        raise ScenarioValidationError(
            "场景目录不存在",
            [str(base)],
        )
    files = sorted([*base.glob("*.yaml"), *base.glob("*.yml")])
    if not files:
        raise ScenarioValidationError("场景目录为空", [str(base)])

    errors: list[str] = []
    loaded: list[dict[str, Any]] = []
    for file_path in files:
        try:
            loaded.append(load_scenario_file(file_path))
        except ScenarioValidationError as exc:
            errors.extend(exc.details or [str(exc)])

    if errors:
        raise ScenarioValidationError("场景库校验失败，未载入任何场景", errors)

    for item in loaded:
        existing = db.get(Scenario, item["id"])
        if existing is None:
            existing = Scenario(id=item["id"])
            db.add(existing)
        existing.name = item["name"]
        existing.category = item["category"]
        existing.description = item["description"]
        existing.definition = item["definition"]
        existing.expected = item["expected"]
        existing.alert_count = len(item["definition"]["alerts"])
        existing.log_count = len(item["definition"]["logs"])
        existing.source_file = item["source_file"]
    db.commit()
    return len(loaded)


def list_scenarios(db: Session) -> list[Scenario]:
    return list(db.execute(select(Scenario).order_by(Scenario.id.asc())).scalars().all())


def get_scenario(db: Session, scenario_id: str) -> Scenario:
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise AppError("场景不存在", code="not_found", status_code=404)
    return scenario


def scenario_to_dict(scenario: Scenario, *, include_definition: bool = False) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": scenario.id,
        "name": scenario.name,
        "category": scenario.category,
        "description": scenario.description,
        "alert_count": scenario.alert_count,
        "log_count": scenario.log_count,
        "expected": scenario.expected or {},
        "compression": float((scenario.definition or {}).get("compression") or 10.0),
        "source_file": scenario.source_file,
        "created_at": scenario.created_at,
    }
    if include_definition:
        data["definition"] = scenario.definition or {}
    return data

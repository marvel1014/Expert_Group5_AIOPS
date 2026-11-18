"""诊断报告：确定性骨架 + 可选 LLM 增强的分步排查，版本化与导出。"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.time import as_utc, utcnow
from app.diagnosis.decision import load_decision_model
from app.diagnosis.llm import LLMError, build_llm_client, parse_json_object
from app.diagnosis.service import get_latest_diagnosis
from app.models import Alert, Diagnosis, Incident, IncidentAlert, Report
logger = logging.getLogger(__name__)

RISK_ORDER = {"low": 1, "medium": 2, "high": 3}

# 高风险操作关键词：命中后强制 risk=high 并要求人工确认，模型无权降低风险等级
HIGH_RISK_KEYWORDS = (
    "重启",
    "restart",
    "reboot",
    "删除",
    "delete",
    "drop ",
    "truncate",
    "rm -rf",
    "kill",
    "修改配置",
    "变更配置",
    "modify config",
    "edit config",
    "change config",
    "update config",
    "停止服务",
    "systemctl stop",
    "清空",
    "重置",
)

_FALLBACK_ROLLBACK = "回滚到变更前状态（恢复备份/上一版本配置），观察 5 分钟并确认核心指标恢复。"

_VERIFY_KEYWORDS = ("验证", "确认恢复", "verify", "观察")


def _category_labels() -> dict[str, str]:
    try:
        model = load_decision_model()
    except AppError:
        return {}
    return {name: rule.label for name, rule in model.categories.items()}


def _category_label(category: str | None) -> str:
    if not category:
        return "未知故障类别"
    return _category_labels().get(category, category)


# ------------------------------ 步骤模板 ------------------------------

_TEMPLATE_STEPS: dict[str, list[dict[str, str]]] = {
    "db_connection_pool_exhausted": [
        {
            "title": "确认连接池占用",
            "action": "在数据库节点执行 SHOW PROCESSLIST 与 SHOW STATUS LIKE 'Threads_connected'，统计活跃连接数与来源应用。",
            "expected": "定位占用连接最多的应用与慢 SQL，确认连接数接近 max_connections。",
            "risk": "low",
            "rollback": "只读排查，无需回滚。",
        },
        {
            "title": "定位异常 SQL",
            "action": "开启慢查询日志，筛选执行时间超过阈值的 SQL，确认是否存在连接未释放或长事务。",
            "expected": "找到导致连接堆积的慢 SQL 或未关闭的连接。",
            "risk": "low",
            "rollback": "关闭慢查询日志即可恢复。",
        },
        {
            "title": "释放空闲连接",
            "action": "在低峰期清理长时间空闲（Sleep 超过 600 秒）的数据库会话，释放连接池。",
            "expected": "活跃连接数回落到安全水位，业务查询恢复。",
            "risk": "medium",
            "rollback": "被清理会话的业务请求会自动重连；如异常可回滚应用版本。",
        },
        {
            "title": "调整连接池配置并重启数据库",
            "action": "修改数据库与应用连接池 max_connections 配置，然后重启 mysql-primary 服务使配置生效。",
            "expected": "连接池上限提升，Too many connections 告警消失。",
            "risk": "high",
            "rollback": "恢复原 max_connections 配置并重启服务；重启前确认主从状态与业务低峰窗口。",
        },
    ],
    "network_partition": [
        {
            "title": "确认链路连通性",
            "action": "从 order-service 所在主机执行 ping / mtr / telnet 到 mysql-primary 端口，确认丢包与路由。",
            "expected": "明确丢包位置（主机、交换机或安全组）。",
            "risk": "low",
            "rollback": "只读排查，无需回滚。",
        },
        {
            "title": "检查网络策略与 DNS",
            "action": "检查安全组、网络 ACL、DNS 解析记录是否变更，确认连接重置与域名解析失败的来源。",
            "expected": "找到被阻断的规则或错误的 DNS 记录。",
            "risk": "low",
            "rollback": "只读排查，无需回滚。",
        },
        {
            "title": "恢复网络链路",
            "action": "修改安全组或网络策略，放行 order-service 到 mysql-primary 的流量；必要时切换备用链路。",
            "expected": "连接重置与丢包告警消失，请求成功率恢复。",
            "risk": "high",
            "rollback": "恢复变更前的安全组/网络策略，并回切原链路。",
        },
    ],
    "disk_full": [
        {
            "title": "确认磁盘占用",
            "action": "在 db-host-01 执行 df -h 与 du -sh 定位写满的分区与目录。",
            "expected": "确认 /var/lib/mysql 等分区使用率 100% 的根因目录。",
            "risk": "low",
            "rollback": "只读排查，无需回滚。",
        },
        {
            "title": "清理可回收空间",
            "action": "删除数据库旧 binlog、归档日志与临时文件，释放磁盘空间。",
            "expected": "磁盘使用率回落到 80% 以下，MySQL 恢复写入。",
            "risk": "high",
            "rollback": "删除前确认 binlog 已备份且从库已同步；如误删，从备份恢复并重建从库。",
        },
        {
            "title": "扩容磁盘",
            "action": "为 db-host-01 扩容数据盘或挂载新卷，并调整文件系统容量。",
            "expected": "可用空间满足未来 30 天增长，磁盘告警解除。",
            "risk": "medium",
            "rollback": "保留原卷快照，异常时回滚挂载点与文件系统配置。",
        },
    ],
    "cpu_overload": [
        {
            "title": "定位 CPU 热点",
            "action": "在 order-host-01 执行 top / pidstat 与容器监控，找出 CPU 占用最高的进程或容器。",
            "expected": "确认是业务进程、批处理任务还是异常循环导致 CPU 饱和。",
            "risk": "low",
            "rollback": "只读排查，无需回滚。",
        },
        {
            "title": "限制异常进程资源",
            "action": "通过 cgroup 或容器资源限制为异常进程设置 CPU quota，必要时降级非核心任务。",
            "expected": "CPU 使用率回到 80% 以下，服务延迟恢复。",
            "risk": "medium",
            "rollback": "恢复原 cgroup/容器资源限制配置。",
        },
        {
            "title": "重启高负载服务",
            "action": "在业务低峰期重启 order-service 进程，释放异常线程与内存。",
            "expected": "CPU 负载下降，P99 延迟恢复正常。",
            "risk": "high",
            "rollback": "重启前确认实例可摘流量；异常时回滚到上一版本并恢复原实例。",
        },
    ],
    "service_crash": [
        {
            "title": "查看崩溃现场",
            "action": "查看 payment-service 容器日志、退出码与 OOMKilled 事件，确认崩溃原因。",
            "expected": "明确崩溃是 OOM、空指针还是启动配置错误。",
            "risk": "low",
            "rollback": "只读排查，无需回滚。",
        },
        {
            "title": "回滚异常版本",
            "action": "将 payment-service 回滚到上一个稳定版本镜像，并摘除异常实例流量。",
            "expected": "进程不再反复退出，CrashLoopBackOff 结束。",
            "risk": "high",
            "rollback": "保留新版本镜像与发布记录；确认回滚版本功能正常后再切换流量。",
        },
        {
            "title": "调整资源与重启服务",
            "action": "提高 payment-service 内存 limit，然后重启服务实例恢复副本数。",
            "expected": "服务副本全部 Ready，支付成功率恢复。",
            "risk": "high",
            "rollback": "恢复原资源 limit 与副本数配置。",
        },
    ],
    "config_error": [
        {
            "title": "确认非法配置项",
            "action": "对比 order-service 最近一次配置变更，定位 order.payment.timeout=-1 与未知配置键。",
            "expected": "确认错误配置的来源与影响范围。",
            "risk": "low",
            "rollback": "只读排查，无需回滚。",
        },
        {
            "title": "回滚配置",
            "action": "将 order-service 配置回滚到上一个合法版本并重新加载配置。",
            "expected": "服务启动成功，配置错误告警消失。",
            "risk": "high",
            "rollback": "保留新旧配置版本；回滚失败时恢复原配置文件并重启。",
        },
        {
            "title": "补充配置校验",
            "action": "在 CI 中加入配置 Schema 校验与非法值检查，阻止错误配置再次发布。",
            "expected": "后续配置变更在发布前被拦截。",
            "risk": "medium",
            "rollback": "如校验规则误伤，可回滚 CI 配置。",
        },
    ],
    "dependency_timeout": [
        {
            "title": "定位超时依赖",
            "action": "查看调用链与依赖监控，确认超时发生在哪个下游服务与接口。",
            "expected": "明确超时依赖与 P99 延迟分布。",
            "risk": "low",
            "rollback": "只读排查，无需回滚。",
        },
        {
            "title": "调整超时与重试策略",
            "action": "调整下游调用超时时间与重试退避策略，避免雪崩。",
            "expected": "请求失败率下降，超时告警减少。",
            "risk": "medium",
            "rollback": "恢复原有超时与重试配置。",
        },
        {
            "title": "重启异常依赖服务",
            "action": "在低峰期重启出现线程池耗尽的下游服务实例。",
            "expected": "下游恢复健康，调用超时消失。",
            "risk": "high",
            "rollback": "重启前确认流量已摘除；异常时回滚上一版本。",
        },
    ],
}

_GENERIC_STEPS = [
    {
        "title": "确认事件影响范围",
        "action": "查看事件关联告警、涉及节点与最近变更记录，确认故障影响面。",
        "expected": "明确受影响服务、节点与用户范围。",
        "risk": "low",
        "rollback": "只读排查，无需回滚。",
    },
    {
        "title": "定位根因证据",
        "action": "结合告警时间线与日志错误签名，确认故障起点与传播路径。",
        "expected": "锁定根因节点与故障类别。",
        "risk": "low",
        "rollback": "只读排查，无需回滚。",
    },
    {
        "title": "执行修复操作",
        "action": "按根因结论修复故障节点，必要时重启相关服务或回滚变更。",
        "expected": "告警收敛，业务指标恢复。",
        "risk": "high",
        "rollback": "执行前备份并确认回滚方案；异常时回滚到变更前状态。",
    },
]

_VERIFICATION_TEXT: dict[str, str] = {
    "db_connection_pool_exhausted": "观察数据库 Threads_connected 回落、连接等待超时消失，业务查询 P99 恢复正常并持续 10 分钟无新增告警。",
    "network_partition": "从 order-service 到 mysql-primary 的连通性恢复、丢包率归零，连接重置与 DNS 错误消失。",
    "disk_full": "df -h 显示目标分区使用率低于 80%，MySQL 写入正常，磁盘与 inode 告警消失。",
    "cpu_overload": "order-host-01 CPU 使用率低于 80%，load average 回落，服务 P99 延迟恢复基线。",
    "service_crash": "payment-service 副本全部 Ready，restartCount 不再增长，支付成功率恢复。",
    "config_error": "order-service 配置校验通过、启动成功，配置相关错误日志消失。",
    "dependency_timeout": "依赖调用超时率与 P99 延迟回落，下游服务健康检查全部通过。",
}


def _template_steps(category: str | None) -> list[dict[str, str]]:
    steps = _TEMPLATE_STEPS.get(category or "") or _GENERIC_STEPS
    return [dict(step) for step in steps]


# ------------------------------ LLM 增强步骤 ------------------------------


def _normalize_steps(raw_steps: Any) -> list[dict[str, str]]:
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("步骤必须是 JSON 数组")
    steps: list[dict[str, str]] = []
    for index, raw in enumerate(raw_steps):
        if not isinstance(raw, dict):
            raise ValueError(f"步骤 {index} 必须是对象")
        action = str(raw.get("action") or "").strip()
        title = str(raw.get("title") or "").strip()
        expected = str(raw.get("expected") or "").strip()
        risk = str(raw.get("risk") or "medium").strip().lower()
        rollback = str(raw.get("rollback") or "").strip()
        if not action:
            raise ValueError(f"步骤 {index} 缺少 action")
        if risk not in RISK_ORDER:
            raise ValueError(f"步骤 {index} risk 非法: {risk}")
        if not rollback:
            rollback = _FALLBACK_ROLLBACK
        steps.append(
            {
                "title": title or f"步骤 {index + 1}",
                "action": action,
                "expected": expected or "故障现象缓解。",
                "risk": risk,
                "rollback": rollback,
            }
        )
    return steps


def _llm_generate_steps(incident: Incident, diagnosis: Diagnosis) -> list[dict[str, str]]:
    """调用大模型生成分步排查与修复；失败抛出异常由调用方回退模板。"""
    client = build_llm_client("llm")
    system = (
        "你是资深 SRE。请基于故障事件与根因诊断给出分步排查与修复建议，"
        "只输出 JSON 对象：{\"steps\":[{\"title\":string,\"action\":string,"
        "\"expected\":string,\"risk\":\"low|medium|high\",\"rollback\":string}]}。"
        "高危操作必须标记 risk=high 并给出回滚动作。"
    )
    payload = {
        "incident": {
            "id": incident.id,
            "title": incident.title,
            "severity": incident.severity,
            "nodes": incident.nodes or [],
        },
        "diagnosis": {
            "category": diagnosis.root_cause_category,
            "node": diagnosis.root_cause_node,
            "confidence": diagnosis.confidence,
            "reasoning": diagnosis.reasoning,
        },
        "steps": "3~5 步，由低风险排查到高风险修复，每步含回滚动作",
    }
    response = client.complete(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
    )
    data = parse_json_object(response.content)
    return _normalize_steps(data.get("steps"))


def _apply_risk_rules(
    steps: list[dict[str, str]],
) -> tuple[list[dict[str, str]], bool]:
    """强制高风险标注与人工确认，排序并重新编号。"""
    high_risk_found = False
    for step in steps:
        text = f"{step.get('title', '')} {step.get('action', '')} {step.get('expected', '')}".lower()
        if any(keyword.lower() in text for keyword in HIGH_RISK_KEYWORDS):
            step["risk"] = "high"
            step["manual_confirm"] = True
            high_risk_found = True
        if not step.get("rollback"):
            step["rollback"] = _FALLBACK_ROLLBACK
        step["risk"] = str(step.get("risk") or "medium").lower()
        if step["risk"] not in RISK_ORDER:
            step["risk"] = "medium"
        if step["risk"] == "high":
            high_risk_found = True

    def sort_key(item: tuple[int, dict[str, str]]) -> tuple[int, int, int]:
        index, step = item
        text = f"{step.get('title', '')} {step.get('action', '')}"
        is_verify = 1 if any(keyword in text for keyword in _VERIFY_KEYWORDS) else 0
        return (is_verify, RISK_ORDER[step["risk"]], index)

    ordered = [step for _, step in sorted(enumerate(steps), key=sort_key)]
    for index, step in enumerate(ordered, start=1):
        step["order"] = index
    return ordered, high_risk_found


# ------------------------------ 报告生成 ------------------------------


def _evidence_lines(db: Session, incident: Incident, diagnosis: Diagnosis) -> list[str]:
    lines: list[str] = []
    evidence = diagnosis.evidence or {}
    for alert in (evidence.get("alerts") or [])[:8]:
        lines.append(
            f"告警 #{alert.get('id')} [{alert.get('severity')}] "
            f"{alert.get('host')}/{alert.get('service') or '-'}: {alert.get('rule')} "
            f"—— {str(alert.get('message') or '')[:120]}"
        )
    for log in (evidence.get("logs") or [])[:6]:
        lines.append(
            f"日志 #{log.get('id')} [{log.get('level')}] "
            f"{log.get('host')}/{log.get('service') or '-'}: "
            f"{str(log.get('message') or '')[:120]}"
        )
    if not lines:
        alert_ids = db.execute(
            select(IncidentAlert.alert_id).where(
                IncidentAlert.incident_id == incident.id
            )
        ).scalars().all()
        alerts = (
            list(db.execute(select(Alert).where(Alert.id.in_(alert_ids))).scalars().all())
            if alert_ids
            else []
        )
        for alert in alerts[:5]:
            lines.append(
                f"告警 #{alert.id} [{alert.severity}] {alert.host}: {alert.rule}"
            )
    if not lines:
        lines.append("暂无结构化证据，请结合事件时间线与监控排查。")
    return lines


def _build_content(
    db: Session,
    incident: Incident,
    diagnosis: Diagnosis,
    *,
    steps: list[dict[str, str]],
    high_risk_found: bool,
    mode: str,
) -> tuple[dict[str, Any], str]:
    category = diagnosis.root_cause_category
    label = _category_label(category)
    node = diagnosis.root_cause_node or (incident.nodes or ["未知节点"])[0]
    degraded = diagnosis.source == "degraded" or diagnosis.status == "degraded"
    needs_review = bool(diagnosis.needs_review) or degraded
    confidence = round(float(diagnosis.confidence or 0.0), 4)
    prefix = "可能" if needs_review else ""
    suffix = "（低置信度结论，待人工确认）" if needs_review else ""

    summary = (
        f"{prefix}事件 #{incident.id}「{incident.title or '未命名事件'}」的根因"
        f"为 {label}，根因节点 {node}，置信度 {confidence}{suffix}。"
        f"事件涉及 {len(incident.nodes or [])} 个节点、{incident.alert_count} 条告警，"
        f"最高严重级别 {incident.severity}。"
    )
    impact = (
        f"影响节点：{', '.join(incident.nodes or []) or '未知'}；"
        f"故障时间窗口：{as_utc(incident.window_start).isoformat()} ~ "
        f"{as_utc(incident.window_end).isoformat()}；"
        f"受影响告警数：{incident.alert_count}；拓扑关联："
        f"{'已建立' if incident.topology_linked else '未建立（降级）'}。"
    )
    reasoning = diagnosis.reasoning or "诊断未提供推理说明。"
    root_cause = (
        f"{prefix}根因类别：{label}（{category or '未知'}）；根因节点：{node}；"
        f"置信度：{confidence}。{suffix}\n诊断推理：{reasoning}"
    )
    if degraded:
        root_cause += "\n注意：本结论来自降级诊断（大模型不可用，使用决策模型与模板推理）。"

    evidence = _evidence_lines(db, incident, diagnosis)
    verification = _VERIFICATION_TEXT.get(
        category or "",
        "确认核心业务指标与告警恢复，持续观察 10 分钟无新增同类告警。",
    )
    rollback_lines = [
        step["rollback"] for step in steps if step.get("risk") == "high"
    ]
    rollback = (
        "高风险操作的回滚方式：\n- " + "\n- ".join(rollback_lines)
        if rollback_lines
        else "本次建议以只读排查与低风险操作为主，如变更请提前备份并准备回滚方案。"
    )

    review_notice = ""
    if needs_review:
        missing = diagnosis.missing_evidence or []
        review_notice = (
            "⚠️ 人工复核提示：本诊断置信度较低或关键证据缺失，结论为"
            f"{'「可能」' if needs_review else ''}推断，请人工复核后再执行修复。"
            + (f" 缺失证据：{'；'.join(missing)}。" if missing else "")
        )
        if degraded:
            review_notice += " 本报告由降级诊断生成。"
    if high_risk_found:
        risk_notice = (
            "⚠️ 本报告包含高风险操作（重启服务、删除数据或修改配置），"
            "必须由值班负责人人工确认后执行，并提前备份与准备回滚方案。"
        )
    else:
        risk_notice = "本报告建议以只读排查与低风险操作为主；涉及变更时请提前备份。"

    content: dict[str, Any] = {
        "summary": summary,
        "impact": impact,
        "root_cause": root_cause,
        "evidence": evidence,
        "steps": steps,
        "verification": verification,
        "rollback": rollback,
        "risk_notice": risk_notice,
        "llm_enhanced": mode == "llm",
        "diagnosis_source": diagnosis.source,
        "confidence": confidence,
        "generated_at": utcnow().isoformat(),
    }
    if review_notice:
        content["review_notice"] = review_notice
    return content, label


def render_markdown(
    report: Report, content: dict[str, Any], incident: Incident, diagnosis: Diagnosis
) -> str:
    lines: list[str] = []
    lines.append(f"# 故障诊断报告：{incident.title or f'事件 #{incident.id}'}")
    lines.append("")
    lines.append(
        f"> 事件 #{incident.id} · 报告版本 v{report.version} · "
        f"风险等级 {report.risk_level} · 生成时间 {as_utc(report.created_at).isoformat()}"
    )
    lines.append(
        f"> 诊断版本 v{diagnosis.version} · 结论来源 {diagnosis.source} · "
        f"置信度 {diagnosis.confidence}"
    )
    lines.append("")
    if content.get("review_notice"):
        lines.append("## ⚠️ 人工复核提示")
        lines.append("")
        lines.append(str(content["review_notice"]))
        lines.append("")
    sections = [
        ("一、故障摘要", "summary"),
        ("二、影响范围", "impact"),
        ("三、根因结论", "root_cause"),
    ]
    for title, key in sections:
        lines.append(f"## {title}")
        lines.append("")
        lines.append(str(content.get(key) or ""))
        lines.append("")
    lines.append("## 四、证据引用")
    lines.append("")
    for item in content.get("evidence") or []:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 五、分步排查与修复")
    lines.append("")
    for step in content.get("steps") or []:
        lines.append(f"### {step.get('order')}. {step.get('title')}")
        lines.append("")
        lines.append(f"- **操作**：{step.get('action')}")
        lines.append(f"- **预期结果**：{step.get('expected')}")
        risk_text = step.get("risk")
        if step.get("manual_confirm"):
            risk_text += "（高风险，需人工确认）"
        lines.append(f"- **风险等级**：{risk_text}")
        lines.append(f"- **回滚动作**：{step.get('rollback')}")
        lines.append("")
    lines.append("## 六、验证方式")
    lines.append("")
    lines.append(str(content.get("verification") or ""))
    lines.append("")
    lines.append("## 七、回滚方案")
    lines.append("")
    lines.append(str(content.get("rollback") or ""))
    lines.append("")
    lines.append("## 八、风险提示")
    lines.append("")
    lines.append(str(content.get("risk_notice") or ""))
    lines.append("")
    return "\n".join(lines)


def _build_steps(
    incident: Incident,
    diagnosis: Diagnosis,
    *,
    mode: str,
) -> tuple[list[dict[str, str]], bool, bool]:
    """返回 (steps, 是否含高风险, 是否成功使用 LLM 增强)。"""
    category = diagnosis.root_cause_category
    effective_mode = mode or settings.effective_llm_mode
    if effective_mode == "llm" and settings.llm_configured:
        try:
            steps = _normalize_steps(_llm_generate_steps(incident, diagnosis))
            normalized, high_risk = _apply_risk_rules(steps)
            return normalized, high_risk, True
        except Exception as exc:  # LLM/解析/结构校验失败均回退模板
            logger.warning("报告 LLM 增强失败，回退内置模板: %s", exc)
    steps, high_risk = _apply_risk_rules(_template_steps(category))
    return steps, high_risk, False


def generate_report(
    db: Session,
    incident_id: int,
    diagnosis_id: int | None = None,
    *,
    mode: str | None = None,
) -> Report:
    """基于指定/最新诊断生成报告；版本递增，默认返回最新版本。"""
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise AppError("事件不存在", code="not_found", status_code=404)

    if diagnosis_id is not None:
        diagnosis = db.get(Diagnosis, diagnosis_id)
        if diagnosis is None or diagnosis.incident_id != incident_id:
            raise AppError("诊断记录不存在", code="not_found", status_code=404)
    else:
        diagnosis = get_latest_diagnosis(db, incident_id)
        if diagnosis is None:
            raise AppError(
                "事件尚无诊断结果，请先执行诊断",
                code="diagnosis_not_found",
                status_code=404,
            )

    steps, high_risk_found, llm_enhanced = _build_steps(
        incident, diagnosis, mode=mode or ""
    )
    content, _label = _build_content(
        db,
        incident,
        diagnosis,
        steps=steps,
        high_risk_found=high_risk_found,
        mode="llm" if llm_enhanced else "template",
    )
    risks = [step.get("risk") for step in steps]
    if "high" in risks:
        risk_level = "high"
    elif "medium" in risks:
        risk_level = "medium"
    else:
        risk_level = "low"

    latest_version = db.execute(
        select(Report.version)
        .where(Report.incident_id == incident_id)
        .order_by(Report.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    report = Report(
        incident_id=incident_id,
        diagnosis_id=diagnosis.id,
        version=(latest_version or 0) + 1,
        risk_level=risk_level,
        content=content,
        markdown="",
    )
    db.add(report)
    db.flush()
    report.markdown = render_markdown(report, content, incident, diagnosis)
    db.commit()
    return report


def serialize_report(report: Report) -> dict[str, Any]:
    return {
        "id": report.id,
        "incident_id": report.incident_id,
        "diagnosis_id": report.diagnosis_id,
        "version": report.version,
        "risk_level": report.risk_level,
        "content": report.content or {},
        "markdown": report.markdown,
        "created_at": report.created_at,
    }


def get_report(db: Session, report_id: int) -> Report:
    report = db.get(Report, report_id)
    if report is None:
        raise AppError("报告不存在", code="not_found", status_code=404)
    return report


def export_report(db: Session, report_id: int, fmt: str) -> tuple[str, str, str]:
    """返回 (内容, media_type, 文件名)；文件名含事件与版本号，UTF-8 编码。"""
    report = get_report(db, report_id)
    normalized = (fmt or "markdown").lower()
    if normalized in ("markdown", "md"):
        content = report.markdown or "# 报告内容为空"
        filename = f"report-incident{report.incident_id}-v{report.version}.md"
        return content, "text/markdown; charset=utf-8", filename
    if normalized == "json":
        content = json.dumps(
            serialize_report(report), ensure_ascii=False, indent=2, default=str
        )
        filename = f"report-incident{report.incident_id}-v{report.version}.json"
        return content, "application/json; charset=utf-8", filename
    raise AppError(
        "不支持的导出格式，仅支持 markdown 或 json",
        code="validation_error",
        status_code=422,
    )

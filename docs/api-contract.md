# API 契约（前后端约定）

所有接口前缀 `/api/v1`（`/healthz` 除外）。时间字段为 ISO-8601 UTC 字符串。错误统一为：

```json
{"error": {"code": "validation_error", "message": "请求参数校验失败", "details": []}}
```

分页响应统一为 `{items, total, page, page_size}`。

## 健康检查

`GET /healthz` → `{status, version, database, llm_mode, llm_configured}`

## 告警与日志

`POST /api/v1/alerts/webhook`，单条对象或 `{"alerts": [...]}`：

```json
{
  "source": "webhook",
  "host": "db-host-01",
  "service": "mysql-primary",
  "rule": "MySQL connection pool exhausted",
  "severity": "critical",
  "message": "Too many connections (151/151)",
  "labels": {"env": "prod"},
  "occurred_at": "2026-11-18T09:00:00Z"
}
```

单条响应 `{accepted, duplicates, alert_id, alert}`；批量响应 `{accepted, duplicates, rejected, alert_ids, results:[{index, accepted, duplicate?, alert_id?, error?}]}`。

`GET /api/v1/alerts?host=&service=&severity=&status=&batch_id=&start=&end=&page=&page_size=`

告警对象：

```json
{
  "id": 1, "fingerprint": "...", "host": "db-host-01", "service": "mysql-primary",
  "rule": "...", "rule_family": "db_connection", "severity": "critical",
  "message": "...", "labels": {}, "occurred_at": "...", "first_seen": "...",
  "last_seen": "...", "repeat_count": 3, "cluster_id": 2, "status": "open",
  "batch_id": null
}
```

`GET /api/v1/alerts/{id}`

`POST /api/v1/logs`，单条或 `{"logs": [...]}`：

```json
{"ts": "...", "host": "db-host-01", "service": "mysql-primary", "level": "ERROR",
 "message": "TooManyConnectionsError", "stack": "Traceback ...", "trace_id": "trace-123"}
```

`GET /api/v1/logs?host=&service=&trace_id=&level=&start=&end=&limit=` → `{items, count}`

## 降噪聚合

`GET /api/v1/clusters?host=&service=&status=&start=&end=&page=&page_size=`

聚合对象：

```json
{"id": 1, "fingerprint": "...", "host": "db-host-01", "service": "mysql-primary",
 "rule": "...", "rule_family": "db_connection", "severity": "critical",
 "status": "open", "member_count": 20, "first_seen": "...", "last_seen": "...",
 "resolved_at": null, "reopen_count": 0, "merge_reason": {...}}
```

`GET /api/v1/clusters/{id}` → 聚合对象 + `member_alert_ids` + `members`（告警对象数组）

`POST /api/v1/clusters/{id}/ack`、`POST /api/v1/clusters/{id}/resolve` → 聚合对象

`GET /api/v1/stats/noise?start=&end=` →

```json
{"window": {"start": "...", "end": "..."}, "raw_alert_count": 120,
 "cluster_count": 8, "compression_ratio": 15.0,
 "top_hosts": [{"host": "db-host-01", "raw_count": 60}],
 "top_rule_families": [{"rule_family": "db_connection", "raw_count": 60}]}
```

## 拓扑与故障事件

`GET /api/v1/topology` → `{nodes: [{id, kind, name}], edges: [{upstream, downstream, weight}]}`

`POST /api/v1/topology/reload` → 同上（重新读取 `config/topology.yaml`）

`GET /api/v1/incidents?status=&severity=&batch_id=&start=&end=&page=&page_size=`

事件对象：

```json
{"id": 1, "title": "疑似 mysql-primary 故障：...", "status": "open",
 "severity": "critical", "window_start": "...", "window_end": "...",
 "nodes": ["mysql-primary", "order-service"], "alert_count": 42,
 "topology_linked": true,
 "root_candidates": [{"node": "mysql-primary", "score": 0.9, "severity": "critical",
                      "first_seen": "...", "reasons": ["..."]}],
 "batch_id": null, "created_at": "...",
 "diagnosis_status": "succeeded", "diagnosis_id": 3}
```

`GET /api/v1/incidents/{id}` → 事件对象 + `alerts`（告警数组）+ `topology`

`POST /api/v1/incidents/{id}/ack`、`POST /api/v1/incidents/{id}/resolve` → 事件对象

`GET /api/v1/stats/compression?start=&end=` → `{window, raw_alert_count, incident_count, compression_ratio, topology_hit_rate}`

## 看板概览

`GET /api/v1/stats/overview?start=&end=&window_minutes=60` →

```json
{"window": {"start": "...", "end": "..."}, "alert_count": 120, "incident_count": 8,
 "compression_ratio": 15.0,
 "severity_distribution": {"critical": 10, "high": 20, "medium": 5, "low": 0},
 "topology_hit_rate": 0.75, "diagnosis_accuracy": 0.83,
 "evaluation_id": "eval-...", "generated_at": "..."}
```

## 诊断

`POST /api/v1/incidents/{id}/diagnose?rerun=false` → `{diagnosis_id, incident_id, status}`（立即返回，后台执行；前端每 1~2 秒轮询）

`GET /api/v1/incidents/{id}/diagnoses` → `{items: [诊断对象], total}`

`GET /api/v1/diagnoses/{id}` → 诊断对象：

```json
{"id": 3, "incident_id": 1, "version": 1, "status": "succeeded",
 "source": "llm", "root_cause_category": "db_connection_pool_exhausted",
 "root_cause_node": "mysql-primary", "confidence": 0.86,
 "reasoning": "...", "evidence": {"alerts": [...], "logs": [...]},
 "missing_evidence": [], "needs_review": false, "model": "deepseek-chat",
 "prompt_version": "rca_v1:abc123", "latency_ms": 1200,
 "token_usage": {"prompt_tokens": 800, "completion_tokens": 200},
 "created_at": "...", "error": null}
```

`GET /api/v1/incidents/{id}/fast-diagnosis` →

```json
{"incident_id": 1, "model_version": "rule-v1",
 "candidates": [{"category": "db_connection_pool_exhausted", "score": 0.82,
                 "confidence": 0.82, "features": ["..."]}]}
```

## 报告

`POST /api/v1/incidents/{id}/reports`，body 可选 `{"diagnosis_id": 3}` → 报告对象（201）

`GET /api/v1/reports/{id}` → 报告对象

`GET /api/v1/reports/{id}/export?format=markdown|json` → 文件下载

报告对象：

```json
{"id": 1, "incident_id": 1, "diagnosis_id": 3, "version": 1,
 "risk_level": "high",
 "content": {"summary": "...", "impact": "...", "root_cause": "...",
   "evidence": ["..."], "steps": [{"order": 1, "title": "...", "action": "...",
   "expected": "...", "risk": "low", "rollback": "..."}],
   "verification": "...", "rollback": "...", "risk_notice": "..."},
 "markdown": "# ...", "created_at": "..."}
```

## 场景与评估

`GET /api/v1/scenarios` → `{items: [{id, name, category, description, alert_count, log_count, expected}], total}`

`GET /api/v1/scenarios/{id}` → 场景摘要 + `definition`

`POST /api/v1/scenarios/{id}/replay`，body 可选 `{"compression": 10, "seed": 42}` →

```json
{"id": "batch-...", "scenario_id": "...", "status": "completed",
 "compression": 10.0, "stats": {"alert_count": 20, "log_count": 8,
 "incident_ids": [10, 11]}, "started_at": "..."}
```

`POST /api/v1/evaluations`，body 可选 `{"scenario_ids": ["db-pool-exhausted"], "seed": 42}` → `{id, status}`（当前实现同步执行并返回 completed，前端统一按轮询兼容）

`GET /api/v1/evaluations` → `{items: [评估摘要], total}`

`GET /api/v1/evaluations/{id}` → 评估摘要 + `cases`

评估对象：

```json
{"id": "eval-...", "status": "completed",
 "metrics": {"total": 6, "top1_accuracy": 0.83, "top3_accuracy": 1.0,
   "node_accuracy": 0.83, "avg_latency_ms": 12, "avg_compression_ratio": 18.5},
 "cases": [{"scenario_id": "...", "expected": {...}, "actual": {...},
   "top1_hit": true, "top3_hit": true, "node_hit": true,
   "latency_ms": 10, "error": null}],
 "created_at": "..."}
```

`GET /api/v1/evaluations/{id}/export` → JSON 文件下载

## 模拟注入

`POST /api/v1/simulate`：

```json
{"scenario_id": "db-pool-exhausted", "compression": 10}
```

也支持 `{"mode": "burst", "host": "order-host-01", "service": "order-service", "count": 30, "rule": "CPU usage 98%"}`。

响应：`{batch_id, alert_count, log_count, incident_ids}`，注入完成后自动触发关联。

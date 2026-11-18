#!/usr/bin/env bash
# 端到端演示：启动临时服务 → 注入 → 关联 → 诊断 → 报告 → 导出 → 场景回放 → 评估
set -euo pipefail

cd "$(dirname "$0")/.."
PORT="${PORT:-8099}"
DB_FILE="${DB_FILE:-/tmp/aiops-e2e-$$.db}"
BASE="http://127.0.0.1:${PORT}"
export DATABASE_URL="sqlite:///${DB_FILE}"
export LLM_MODE="${LLM_MODE:-mock}"
export PYTHONPATH="$PWD/.deps:$PWD/backend:$PWD"

cleanup() {
  kill "${SERVER_PID:-}" 2>/dev/null || true
  wait "${SERVER_PID:-}" 2>/dev/null || true
  rm -f "${DB_FILE}" "${DB_FILE}-wal" "${DB_FILE}-shm"
}
trap cleanup EXIT

echo "[1/7] 启动服务 :${PORT}"
python3 -m uvicorn app.main:app --host 127.0.0.1 --port "${PORT}" >/tmp/aiops-e2e-server.log 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 100); do
  if curl -sf "${BASE}/healthz" >/dev/null 2>&1; then break; fi
  sleep 0.2
done
curl -sf "${BASE}/healthz" >/dev/null || { echo "服务启动失败，日志："; tail -30 /tmp/aiops-e2e-server.log; exit 1; }
echo "    healthz OK"

echo "[2/7] 注入 30 条关联告警 + 堆栈日志"
SIM=$(curl -sf -X POST "${BASE}/api/v1/simulate" -H 'Content-Type: application/json' \
  -d '{"mode":"burst","host":"db-host-01","service":"mysql-primary","count":30,"rule":"MySQL connection pool exhausted","severity":"critical"}')
BATCH_ID=$(jq -r '.batch_id' <<<"${SIM}")
INCIDENT_ID=$(jq -r '.incident_ids[0]' <<<"${SIM}")
echo "    batch=${BATCH_ID} incident=${INCIDENT_ID} alerts=$(jq -r '.alert_count' <<<"${SIM}") logs=$(jq -r '.log_count' <<<"${SIM}")"

echo "[3/7] 触发诊断并等待完成"
DIAG_START=$(curl -sf -X POST "${BASE}/api/v1/incidents/${INCIDENT_ID}/diagnose")
DIAG_ID=$(jq -r '.diagnosis_id' <<<"${DIAG_START}")
STATUS=""
for _ in $(seq 1 120); do
  DIAG=$(curl -sf "${BASE}/api/v1/diagnoses/${DIAG_ID}")
  STATUS=$(jq -r '.status' <<<"${DIAG}")
  case "${STATUS}" in succeeded|degraded|failed) break ;; esac
  sleep 0.3
done
[ "${STATUS}" = "succeeded" ] || [ "${STATUS}" = "degraded" ] || { echo "诊断失败: ${STATUS}"; exit 1; }
echo "    diagnosis=${DIAG_ID} status=${STATUS} source=$(jq -r '.source' <<<"${DIAG}") category=$(jq -r '.root_cause_category' <<<"${DIAG}") node=$(jq -r '.root_cause_node' <<<"${DIAG}") confidence=$(jq -r '.confidence' <<<"${DIAG}")"

echo "[4/7] 生成诊断报告并导出 Markdown"
REPORT=$(curl -sf -X POST "${BASE}/api/v1/incidents/${INCIDENT_ID}/reports" -H 'Content-Type: application/json' -d '{}')
REPORT_ID=$(jq -r '.id' <<<"${REPORT}")
STEPS=$(jq -r '.content.steps | length' <<<"${REPORT}")
RISK=$(jq -r '.risk_level' <<<"${REPORT}")
curl -sf -X GET "${BASE}/api/v1/reports/${REPORT_ID}/export?format=markdown" -o /tmp/aiops-report-${REPORT_ID}.md
echo "    report=${REPORT_ID} version=$(jq -r '.version' <<<"${REPORT}") risk=${RISK} steps=${STEPS} export=/tmp/aiops-report-${REPORT_ID}.md"

echo "[5/7] 场景库与回放"
SCENARIOS=$(curl -sf "${BASE}/api/v1/scenarios")
SCENARIO_COUNT=$(jq -r '.total' <<<"${SCENARIOS}")
FIRST_SCENARIO=$(jq -r '.items[0].id' <<<"${SCENARIOS}")
REPLAY=$(curl -sf -X POST "${BASE}/api/v1/scenarios/${FIRST_SCENARIO}/replay" -H 'Content-Type: application/json' -d '{"compression":10,"seed":42}')
echo "    scenarios=${SCENARIO_COUNT} replayed=${FIRST_SCENARIO} alerts=$(jq -r '.stats.alert_count' <<<"${REPLAY}")"

echo "[6/7] 全量评估"
EVAL_START=$(curl -sf -X POST "${BASE}/api/v1/evaluations" -H 'Content-Type: application/json' -d '{"seed":42}')
EVAL_ID=$(jq -r '.id' <<<"${EVAL_START}")
EVAL_STATUS=""
for _ in $(seq 1 200); do
  EVAL=$(curl -sf "${BASE}/api/v1/evaluations/${EVAL_ID}")
  EVAL_STATUS=$(jq -r '.status' <<<"${EVAL}")
  case "${EVAL_STATUS}" in completed|failed) break ;; esac
  sleep 0.3
done
[ "${EVAL_STATUS}" = "completed" ] || { echo "评估失败"; exit 1; }
curl -sf "${BASE}/api/v1/evaluations/${EVAL_ID}/export" -o /tmp/aiops-evaluation-${EVAL_ID}.json

echo "[7/7] 结果汇总"
jq -n \
  --arg batch "${BATCH_ID}" \
  --argjson incident "${INCIDENT_ID}" \
  --arg diagnosis "${DIAG_ID}" \
  --arg category "$(jq -r '.root_cause_category' <<<"${DIAG}")" \
  --arg node "$(jq -r '.root_cause_node' <<<"${DIAG}")" \
  --arg source "$(jq -r '.source' <<<"${DIAG}")" \
  --arg report "${REPORT_ID}" \
  --argjson steps "${STEPS}" \
  --argjson scenarios "${SCENARIO_COUNT}" \
  --argjson metrics "$(jq -c '.metrics' <<<"${EVAL}")" \
  '{batch: $batch, incident_id: $incident, diagnosis: {id: $diagnosis, source: $source, category: $category, node: $node}, report: {id: $report, steps: $steps}, scenarios: $scenarios, evaluation: $metrics}'

echo "✅ 端到端演示通过"

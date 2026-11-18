/** 与 docs/api-contract.md 对齐的接口数据类型。 */

export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info'

export interface TimeWindow {
  start: string
  end: string
}

/** 分页响应统一为 {items, total, page, page_size} */
export interface Paginated<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

// ---------------------------------------------------------------- 告警与日志

export interface Alert {
  id: number
  fingerprint: string
  host: string
  service: string
  rule: string
  rule_family: string | null
  severity: Severity
  message: string
  labels: Record<string, unknown>
  occurred_at: string
  first_seen: string
  last_seen: string
  repeat_count: number
  cluster_id: number | null
  status: string
  batch_id: string | null
}

export interface LogEntry {
  ts?: string
  host?: string
  service?: string
  level?: string
  message?: string
  stack?: string | null
  trace_id?: string | null
  [key: string]: unknown
}

// -------------------------------------------------------------------- 拓扑

export interface TopologyNode {
  id: string
  kind: string
  name: string
}

export interface TopologyEdge {
  upstream: string
  downstream: string
  weight: number
}

export interface Topology {
  nodes: TopologyNode[]
  edges: TopologyEdge[]
}

// -------------------------------------------------------------------- 事件

export interface RootCandidate {
  node: string
  score: number
  severity: Severity
  first_seen: string
  reasons: string[]
}

export interface Incident {
  id: number
  title: string
  status: string
  severity: Severity
  window_start: string
  window_end: string
  nodes: string[]
  alert_count: number
  topology_linked: boolean
  root_candidates: RootCandidate[]
  batch_id: string | null
  created_at: string
}

export interface IncidentDetail extends Incident {
  alerts: Alert[]
  topology: Topology | null
}

// -------------------------------------------------------------------- 诊断

export interface DiagnoseStart {
  diagnosis_id: number
  incident_id: number
  status: string
}

export interface TokenUsage {
  prompt_tokens?: number
  completion_tokens?: number
  [key: string]: number | undefined
}

export interface Diagnosis {
  id: number
  incident_id: number
  version: number
  status: string
  source: string
  root_cause_category: string | null
  root_cause_node: string | null
  confidence: number | null
  reasoning: string
  evidence: Record<string, unknown> | null
  missing_evidence: string[]
  needs_review: boolean
  model: string | null
  prompt_version: string | null
  latency_ms: number | null
  token_usage: TokenUsage | null
  created_at: string
  error: string | null
}

export interface DiagnosisList {
  items: Diagnosis[]
  total: number
}

export interface FastDiagnosisCandidate {
  category: string
  score: number
  confidence: number
  features: string[]
}

export interface FastDiagnosis {
  incident_id: number
  model_version: string
  candidates: FastDiagnosisCandidate[]
}

// -------------------------------------------------------------------- 报告

export interface ReportStep {
  order: number
  title: string
  action: string
  expected: string
  risk: string
  rollback: string
}

export interface ReportContent {
  summary: string
  impact: string
  root_cause: string
  evidence: string[]
  steps: ReportStep[]
  verification: string
  rollback: string
  risk_notice: string
  [key: string]: unknown
}

export interface Report {
  id: number
  incident_id: number
  diagnosis_id: number | null
  version: number
  risk_level: string
  content: ReportContent
  markdown: string
  created_at: string
}

// -------------------------------------------------------------------- 统计

export type SeverityDistribution = Partial<Record<Severity, number>> & {
  [key: string]: number | undefined
}

export interface StatsOverview {
  window: TimeWindow
  alert_count: number
  incident_count: number
  compression_ratio: number
  severity_distribution: SeverityDistribution
  topology_hit_rate: number
  diagnosis_accuracy: number | null
  evaluation_id: string | null
  generated_at: string
}

export interface TopHost {
  host: string
  raw_count: number
}

export interface TopRuleFamily {
  rule_family: string
  raw_count: number
}

export interface NoiseStats {
  window: TimeWindow
  raw_alert_count: number
  cluster_count: number
  compression_ratio: number
  top_hosts: TopHost[]
  top_rule_families: TopRuleFamily[]
}

export interface CompressionStats {
  window: TimeWindow
  raw_alert_count: number
  incident_count: number
  compression_ratio: number
  topology_hit_rate: number
}

// -------------------------------------------------------------- 场景与评估

export interface ScenarioExpected {
  root_cause_category?: string
  root_cause_node?: string
  affected_nodes?: string[]
  [key: string]: unknown
}

export interface Scenario {
  id: string
  name: string
  category: string
  description: string
  alert_count: number
  log_count: number
  expected: ScenarioExpected
}

export interface ScenarioList {
  items: Scenario[]
  total: number
}

export interface EvaluationMetrics {
  total: number
  top1_accuracy: number
  top3_accuracy: number
  node_accuracy: number
  avg_latency_ms: number
  avg_compression_ratio: number
  [key: string]: number
}

export interface EvaluationCase {
  scenario_id: string
  expected: ScenarioExpected
  actual: Record<string, unknown>
  top1_hit: boolean
  top3_hit: boolean
  node_hit: boolean
  latency_ms: number
  error: string | null
}

export interface EvaluationSummary {
  id: string
  status: string
  metrics: EvaluationMetrics | null
  created_at: string
}

export interface Evaluation extends EvaluationSummary {
  cases: EvaluationCase[]
}

export interface EvaluationList {
  items: EvaluationSummary[]
  total: number
}

export interface EvaluationStart {
  id: string
  status: string
}

export interface ScenarioReplayResponse {
  id: string
  scenario_id: string
  status: string
  compression: number
  stats: {
    alert_count: number
    log_count: number
    incident_ids: number[]
  }
  started_at: string
}

// ---------------------------------------------------------------- 模拟注入

export interface SimulateRequest {
  scenario_id?: string
  compression?: number
  mode?: 'burst'
  host?: string
  service?: string
  count?: number
  rule?: string
}

export interface SimulateResponse {
  batch_id: string
  alert_count: number
  log_count: number
  incident_ids: number[]
}

// -------------------------------------------------------------------- 健康

export interface Health {
  status: string
  version: string
  database: string
  llm_mode: string
  llm_configured: boolean
}

/** 统一错误响应体 {error: {code, message, details}} */
export interface ErrorBody {
  error?: {
    code?: string
    message?: string
    details?: unknown
  }
  detail?: unknown
}

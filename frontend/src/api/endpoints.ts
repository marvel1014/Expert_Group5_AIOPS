import { API_BASE, http } from './client'
import type {
  Alert,
  CompressionStats,
  Diagnosis,
  DiagnosisList,
  DiagnoseStart,
  Evaluation,
  EvaluationList,
  EvaluationStart,
  FastDiagnosis,
  Health,
  Incident,
  IncidentDetail,
  NoiseStats,
  Paginated,
  Report,
  Scenario,
  ScenarioList,
  ScenarioReplayResponse,
  SimulateRequest,
  SimulateResponse,
  StatsOverview,
  Topology,
} from './types'

export interface AlertQuery {
  host?: string
  service?: string
  severity?: string
  status?: string
  batch_id?: string
  start?: string
  end?: string
  page?: number
  page_size?: number
}

export interface IncidentQuery {
  status?: string
  severity?: string
  batch_id?: string
  start?: string
  end?: string
  page?: number
  page_size?: number
}

function q(params: object): Record<string, string | number | boolean | null | undefined> {
  return params as unknown as Record<string, string | number | boolean | null | undefined>
}

/** 接口集合：路径与 docs/api-contract.md 一一对应。 */
export const api = {
  // ---------------------------------------------------------------- 健康检查
  health(signal?: AbortSignal): Promise<Health> {
    return http.get<Health>('/healthz', undefined, signal)
  },

  // ------------------------------------------------------------ 告警与事件
  listAlerts(query: AlertQuery, signal?: AbortSignal): Promise<Paginated<Alert>> {
    return http.get<Paginated<Alert>>(`${API_BASE}/alerts`, q(query), signal)
  },
  getAlert(id: number, signal?: AbortSignal): Promise<Alert> {
    return http.get<Alert>(`${API_BASE}/alerts/${id}`, undefined, signal)
  },
  listIncidents(query: IncidentQuery, signal?: AbortSignal): Promise<Paginated<Incident>> {
    return http.get<Paginated<Incident>>(`${API_BASE}/incidents`, q(query), signal)
  },
  getIncident(id: number, signal?: AbortSignal): Promise<IncidentDetail> {
    return http.get<IncidentDetail>(`${API_BASE}/incidents/${id}`, undefined, signal)
  },
  ackIncident(id: number, signal?: AbortSignal): Promise<Incident> {
    return http.post<Incident>(`${API_BASE}/incidents/${id}/ack`, undefined, undefined, signal)
  },
  resolveIncident(id: number, signal?: AbortSignal): Promise<Incident> {
    return http.post<Incident>(`${API_BASE}/incidents/${id}/resolve`, undefined, undefined, signal)
  },

  // ------------------------------------------------------------------ 统计
  statsOverview(signal?: AbortSignal): Promise<StatsOverview> {
    return http.get<StatsOverview>(`${API_BASE}/stats/overview`, undefined, signal)
  },
  statsNoise(signal?: AbortSignal): Promise<NoiseStats> {
    return http.get<NoiseStats>(`${API_BASE}/stats/noise`, undefined, signal)
  },
  statsCompression(signal?: AbortSignal): Promise<CompressionStats> {
    return http.get<CompressionStats>(`${API_BASE}/stats/compression`, undefined, signal)
  },

  // ------------------------------------------------------------------ 拓扑
  getTopology(signal?: AbortSignal): Promise<Topology> {
    return http.get<Topology>(`${API_BASE}/topology`, undefined, signal)
  },

  // ------------------------------------------------------------------ 诊断
  diagnoseIncident(id: number, rerun = false, signal?: AbortSignal): Promise<DiagnoseStart> {
    return http.post<DiagnoseStart>(
      `${API_BASE}/incidents/${id}/diagnose`,
      undefined,
      q({ rerun }),
      signal,
    )
  },
  listDiagnoses(incidentId: number, signal?: AbortSignal): Promise<DiagnosisList> {
    return http.get<DiagnosisList>(
      `${API_BASE}/incidents/${incidentId}/diagnoses`,
      undefined,
      signal,
    )
  },
  getDiagnosis(id: number, signal?: AbortSignal): Promise<Diagnosis> {
    return http.get<Diagnosis>(`${API_BASE}/diagnoses/${id}`, undefined, signal)
  },
  fastDiagnosis(incidentId: number, signal?: AbortSignal): Promise<FastDiagnosis> {
    return http.get<FastDiagnosis>(
      `${API_BASE}/incidents/${incidentId}/fast-diagnosis`,
      undefined,
      signal,
    )
  },

  // ------------------------------------------------------------------ 报告
  createReport(incidentId: number, diagnosisId?: number, signal?: AbortSignal): Promise<Report> {
    return http.post<Report>(
      `${API_BASE}/incidents/${incidentId}/reports`,
      diagnosisId === undefined ? {} : { diagnosis_id: diagnosisId },
      undefined,
      signal,
    )
  },
  getReport(id: number, signal?: AbortSignal): Promise<Report> {
    return http.get<Report>(`${API_BASE}/reports/${id}`, undefined, signal)
  },
  reportExportUrl(id: number, format: 'markdown' | 'json' = 'markdown'): string {
    return `${API_BASE}/reports/${id}/export?format=${format}`
  },

  // -------------------------------------------------------------- 场景与评估
  listScenarios(signal?: AbortSignal): Promise<ScenarioList> {
    return http.get<ScenarioList>(`${API_BASE}/scenarios`, undefined, signal)
  },
  getScenario(id: string, signal?: AbortSignal): Promise<Scenario> {
    return http.get<Scenario>(`${API_BASE}/scenarios/${encodeURIComponent(id)}`, undefined, signal)
  },
  replayScenario(
    id: string,
    body: { compression?: number; seed?: number } = {},
    signal?: AbortSignal,
  ): Promise<ScenarioReplayResponse> {
    return http.post<ScenarioReplayResponse>(
      `${API_BASE}/scenarios/${encodeURIComponent(id)}/replay`,
      body,
      undefined,
      signal,
    )
  },
  startEvaluation(
    body: { scenario_ids?: string[]; seed?: number } = {},
    signal?: AbortSignal,
  ): Promise<EvaluationStart> {
    return http.post<EvaluationStart>(`${API_BASE}/evaluations`, body, undefined, signal)
  },
  listEvaluations(signal?: AbortSignal): Promise<EvaluationList> {
    return http.get<EvaluationList>(`${API_BASE}/evaluations`, undefined, signal)
  },
  getEvaluation(id: string, signal?: AbortSignal): Promise<Evaluation> {
    return http.get<Evaluation>(`${API_BASE}/evaluations/${encodeURIComponent(id)}`, undefined, signal)
  },
  evaluationExportUrl(id: string): string {
    return `${API_BASE}/evaluations/${encodeURIComponent(id)}/export`
  },

  // ---------------------------------------------------------------- 模拟注入
  simulate(body: SimulateRequest, signal?: AbortSignal): Promise<SimulateResponse> {
    return http.post<SimulateResponse>(`${API_BASE}/simulate`, body, undefined, signal)
  },
}

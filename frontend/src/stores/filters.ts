import { defineStore } from 'pinia'
import { reactive } from 'vue'

export interface AlertFilters {
  host: string
  service: string
  severity: string
  status: string
  page: number
  page_size: number
  /** 仅前端排序，不进入 API 查询参数 */
  sort: 'occurred_at_desc' | 'occurred_at_asc' | 'severity_desc' | 'repeat_desc'
}

export interface IncidentFilters {
  status: string
  severity: string
  page: number
  page_size: number
}

/**
 * Pinia 仅保存跨页面复用的筛选条件与分页状态（见 design.md D9），
 * 轮询状态由 useLiveQuery 在页面内维护。
 */
export const useFilterStore = defineStore('filters', () => {
  const alerts = reactive<AlertFilters>({
    host: '',
    service: '',
    severity: '',
    status: '',
    page: 1,
    page_size: 20,
    sort: 'occurred_at_desc',
  })

  const incidents = reactive<IncidentFilters>({
    status: '',
    severity: '',
    page: 1,
    page_size: 20,
  })

  function resetAlerts(): void {
    alerts.host = ''
    alerts.service = ''
    alerts.severity = ''
    alerts.status = ''
    alerts.page = 1
    alerts.sort = 'occurred_at_desc'
  }

  function resetIncidents(): void {
    incidents.status = ''
    incidents.severity = ''
    incidents.page = 1
  }

  return { alerts, incidents, resetAlerts, resetIncidents }
})

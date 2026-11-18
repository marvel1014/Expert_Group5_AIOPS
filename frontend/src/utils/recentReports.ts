import type { Report } from '@/api/types'

export interface RecentReport {
  id: number
  incidentId: number
  title: string
  riskLevel: string
  createdAt: string
}

const STORAGE_KEY = 'aiops.recent_reports'
const MAX_ITEMS = 10

function read(): RecentReport[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw) as unknown
    if (!Array.isArray(parsed)) return []
    return parsed.filter(
      (item): item is RecentReport =>
        typeof item === 'object' && item !== null && typeof (item as RecentReport).id === 'number',
    )
  } catch {
    return []
  }
}

function write(items: RecentReport[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items.slice(0, MAX_ITEMS)))
  } catch {
    // localStorage 不可用时静默忽略（不影响页面功能）
  }
}

export function loadRecentReports(): RecentReport[] {
  return read()
}

export function rememberReport(report: Report, title?: string): void {
  const items = read().filter((item) => item.id !== report.id)
  items.unshift({
    id: report.id,
    incidentId: report.incident_id,
    title: title ?? `事件 #${report.incident_id} 诊断报告`,
    riskLevel: report.risk_level,
    createdAt: report.created_at,
  })
  write(items)
}

export function rememberReportId(id: number, incidentId?: number): void {
  if (read().some((item) => item.id === id)) return
  const items = read()
  items.unshift({
    id,
    incidentId: incidentId ?? 0,
    title: incidentId ? `事件 #${incidentId} 诊断报告` : `报告 #${id}`,
    riskLevel: '',
    createdAt: new Date().toISOString(),
  })
  write(items)
}

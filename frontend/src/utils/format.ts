import type { Severity } from '@/api/types'

export const SEVERITY_OPTIONS: { value: string; label: string }[] = [
  { value: 'critical', label: '严重' },
  { value: 'high', label: '高' },
  { value: 'medium', label: '中' },
  { value: 'low', label: '低' },
  { value: 'info', label: '提示' },
]

export const INCIDENT_STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: 'open', label: '打开' },
  { value: 'acked', label: '已确认' },
  { value: 'resolved', label: '已解决' },
]

export const ALERT_STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: 'open', label: '打开' },
  { value: 'acked', label: '已确认' },
  { value: 'resolved', label: '已解决' },
]

const SEVERITY_LABELS: Record<string, string> = {
  critical: '严重',
  high: '高',
  medium: '中',
  low: '低',
  info: '提示',
}

export function severityLabel(value: string | null | undefined): string {
  if (!value) return '—'
  return SEVERITY_LABELS[value] ?? value
}

export function severityRank(value: string | null | undefined): number {
  switch (value) {
    case 'critical':
      return 0
    case 'high':
      return 1
    case 'medium':
      return 2
    case 'low':
      return 3
    default:
      return 4
  }
}

const STATUS_LABELS: Record<string, string> = {
  open: '打开',
  acked: '已确认',
  acknowledged: '已确认',
  resolved: '已解决',
  closed: '已关闭',
  reopened: '复发',
  pending: '排队中',
  running: '执行中',
  succeeded: '已完成',
  failed: '失败',
  degraded: '已降级',
  completed: '已完成',
  none: '未诊断',
  error: '错误',
}

export function statusLabel(value: string | null | undefined): string {
  if (!value) return '—'
  return STATUS_LABELS[value] ?? value
}

export function statusTone(value: string | null | undefined): 'ok' | 'warn' | 'danger' | 'info' | 'muted' {
  switch (value) {
    case 'succeeded':
    case 'completed':
    case 'resolved':
    case 'ok':
      return 'ok'
    case 'running':
      return 'info'
    case 'pending':
      return 'warn'
    case 'failed':
    case 'error':
      return 'danger'
    case 'degraded':
    case 'reopened':
      return 'warn'
    default:
      return 'muted'
  }
}

export function severityTone(value: string | null | undefined): 'ok' | 'warn' | 'danger' | 'info' | 'muted' {
  switch (value) {
    case 'critical':
      return 'danger'
    case 'high':
      return 'warn'
    case 'medium':
      return 'info'
    case 'low':
      return 'ok'
    default:
      return 'muted'
  }
}

/**
 * 解析后端 ISO-8601 时间。契约约定为 UTC，但部分字段可能不带时区后缀，
 * 若缺少时区则按 UTC 解析，避免按本地时区解释造成 8 小时偏差。
 */
export function parseIsoDate(value: string): Date {
  const hasTimezone = /(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(value)
  return new Date(hasTimezone ? value : `${value}Z`)
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = parseIsoDate(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

export function formatShortTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = parseIsoDate(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleTimeString('zh-CN', { hour12: false })
}

export function formatNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return value.toLocaleString('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function formatPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  // 后端以 0~1 的小数返回比例
  return `${(value * 100).toFixed(digits)}%`
}

export function formatRatio(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${value.toFixed(digits)}x`
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return '—'
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(2)} s`
}

/** ISO 时间转 datetime-local 输入可用格式（本地时区）。 */
export function toLocalInputValue(value: string | null | undefined): string {
  if (!value) return ''
  const date = parseIsoDate(value)
  if (Number.isNaN(date.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`
}

/** 中文相对时间，用于"最近刷新"提示。 */
export function relativeTime(from: Date | null | undefined, now = new Date()): string {
  if (!from) return '—'
  const seconds = Math.max(0, Math.floor((now.getTime() - from.getTime()) / 1000))
  if (seconds < 5) return '刚刚'
  if (seconds < 60) return `${seconds} 秒前`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes} 分钟前`
  return formatTime(from.toISOString())
}

export function isSeverity(value: string): value is Severity {
  return ['critical', 'high', 'medium', 'low', 'info'].includes(value)
}

export function truncate(value: string, max = 80): string {
  if (value.length <= max) return value
  return `${value.slice(0, max)}…`
}

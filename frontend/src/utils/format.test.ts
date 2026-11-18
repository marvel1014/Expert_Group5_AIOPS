import { describe, expect, it } from 'vitest'

import {
  formatDuration,
  formatPercent,
  formatRatio,
  severityLabel,
  severityRank,
  severityTone,
  statusLabel,
  statusTone,
} from './format'

describe('severity', () => {
  it('映射中文标签与色调', () => {
    expect(severityLabel('critical')).toBe('严重')
    expect(severityLabel('high')).toBe('高')
    expect(severityLabel(null)).toBe('—')
    expect(severityTone('critical')).toBe('danger')
    expect(severityTone('low')).toBe('ok')
  })

  it('严重级别排序 critical < high < medium', () => {
    expect(severityRank('critical')).toBeLessThan(severityRank('high'))
    expect(severityRank('high')).toBeLessThan(severityRank('medium'))
  })
})

describe('status', () => {
  it('映射诊断与事件状态', () => {
    expect(statusLabel('succeeded')).toBe('已完成')
    expect(statusLabel('running')).toBe('执行中')
    expect(statusTone('failed')).toBe('danger')
    expect(statusTone('resolved')).toBe('ok')
  })
})

describe('格式化', () => {
  it('比例按 0~1 小数转百分比', () => {
    expect(formatPercent(0.83)).toBe('83.0%')
    expect(formatPercent(null)).toBe('—')
  })

  it('压缩比与耗时格式化', () => {
    expect(formatRatio(15)).toBe('15.0x')
    expect(formatDuration(12)).toBe('12 ms')
    expect(formatDuration(1500)).toBe('1.50 s')
  })
})

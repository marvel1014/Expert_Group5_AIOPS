// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'

import App from './App.vue'
import router from './router'

// jsdom 没有 canvas：mock ECharts 初始化，保留 EChart.vue 组件逻辑本身
vi.mock('echarts/core', () => ({
  use: vi.fn(),
  init: () => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() }),
}))

const alertItem = {
  id: 1,
  fingerprint: 'fp-1',
  host: 'order-host-01',
  service: 'order-service',
  rule: 'CPU usage 98%',
  rule_family: 'cpu_usage',
  severity: 'high',
  message: '模拟故障注入',
  labels: {},
  occurred_at: '2026-11-18T09:00:00Z',
  first_seen: '2026-11-18T09:00:00Z',
  last_seen: '2026-11-18T09:05:00Z',
  repeat_count: 2,
  cluster_id: 1,
  status: 'open',
  batch_id: null,
}

const incidentItem = {
  id: 1,
  title: '疑似 order-service 故障：CPU usage 98%',
  status: 'open',
  severity: 'high',
  window_start: '2026-11-18T09:00:00Z',
  window_end: '2026-11-18T09:05:00Z',
  nodes: ['order-service'],
  alert_count: 5,
  topology_linked: true,
  root_candidates: [
    {
      node: 'order-service',
      score: 0.9,
      severity: 'high',
      first_seen: '2026-11-18T09:00:00Z',
      reasons: ['拓扑入度为 0', '最早告警'],
    },
  ],
  batch_id: null,
  created_at: '2026-11-18T09:01:00Z',
}

const diagnosisItem = {
  id: 1,
  incident_id: 1,
  version: 1,
  status: 'succeeded',
  source: 'llm',
  root_cause_category: 'cpu_overload',
  root_cause_node: 'order-service',
  confidence: 0.86,
  reasoning: 'CPU 指标持续超阈值',
  evidence: { alerts: [1], logs: [] },
  missing_evidence: [],
  needs_review: false,
  model: 'mock-rule-fusion',
  prompt_version: 'rca_v1:abc',
  latency_ms: 12,
  token_usage: { prompt_tokens: 10, completion_tokens: 5 },
  created_at: '2026-11-18T09:02:00Z',
  error: null,
}

const reportItem = {
  id: 1,
  incident_id: 1,
  diagnosis_id: 1,
  version: 1,
  risk_level: 'high',
  content: {
    summary: '订单服务 CPU 过载',
    impact: '下单链路延迟升高',
    root_cause: 'cpu_overload',
    evidence: ['CPU 使用率 98%'],
    steps: [
      {
        order: 1,
        title: '扩容',
        action: '增加副本数',
        expected: 'CPU 回落',
        risk: 'low',
        rollback: '缩容回原副本数',
      },
    ],
    verification: '观察 CPU 5 分钟',
    rollback: '回滚部署',
    risk_notice: '高风险操作需人工确认',
  },
  markdown: '# 诊断报告\n\n## 摘要\n订单服务 CPU 过载',
  created_at: '2026-11-18T09:03:00Z',
}

const evaluationSummary = {
  id: 'eval-1',
  status: 'completed',
  metrics: {
    total: 6,
    top1_accuracy: 0.83,
    top3_accuracy: 1.0,
    node_accuracy: 0.83,
    avg_latency_ms: 12,
    avg_compression_ratio: 18.5,
  },
  created_at: '2026-11-18T09:04:00Z',
}

const scenarioItem = {
  id: 'db-pool-exhausted',
  name: '连接池耗尽',
  category: 'db_connection_pool_exhausted',
  description: '数据库连接池耗尽',
  alert_count: 20,
  log_count: 8,
  expected: {
    root_cause_category: 'db_connection_pool_exhausted',
    root_cause_node: 'mysql-primary',
  },
}

function responseFor(url: string): unknown {
  const path = url.split('?')[0] ?? ''
  if (path === '/healthz') {
    return {
      status: 'ok',
      version: '0.1.0',
      database: 'ok',
      llm_mode: 'mock',
      llm_configured: false,
    }
  }
  if (path === '/api/v1/stats/overview') {
    return {
      window: { start: '2026-11-18T09:00:00Z', end: '2026-11-18T10:00:00Z' },
      alert_count: 5,
      incident_count: 1,
      compression_ratio: 5,
      severity_distribution: { critical: 0, high: 5, medium: 0, low: 0 },
      topology_hit_rate: 1,
      diagnosis_accuracy: 0.83,
      evaluation_id: 'eval-1',
      generated_at: '2026-11-18T10:00:00Z',
    }
  }
  if (path === '/api/v1/stats/noise') {
    return {
      window: { start: '2026-11-18T09:00:00Z', end: '2026-11-18T10:00:00Z' },
      raw_alert_count: 5,
      cluster_count: 1,
      compression_ratio: 5,
      top_hosts: [{ host: 'order-host-01', raw_count: 5 }],
      top_rule_families: [{ rule_family: 'cpu_usage', raw_count: 5 }],
    }
  }
  if (path === '/api/v1/stats/compression') {
    return {
      window: { start: '2026-11-18T09:00:00Z', end: '2026-11-18T10:00:00Z' },
      raw_alert_count: 5,
      incident_count: 1,
      compression_ratio: 5,
      topology_hit_rate: 1,
    }
  }
  if (path === '/api/v1/alerts') {
    return { items: [alertItem], total: 1, page: 1, page_size: 20 }
  }
  if (path === '/api/v1/incidents') {
    return { items: [incidentItem], total: 1, page: 1, page_size: 20 }
  }
  if (/^\/api\/v1\/incidents\/\d+\/diagnoses$/.test(path)) {
    return { items: [diagnosisItem], total: 1 }
  }
  if (/^\/api\/v1\/incidents\/\d+\/fast-diagnosis$/.test(path)) {
    return {
      incident_id: 1,
      model_version: 'rule-v1',
      candidates: [{ category: 'cpu_overload', score: 0.75, confidence: 0.75, features: ['rule'] }],
    }
  }
  if (/^\/api\/v1\/incidents\/\d+$/.test(path)) {
    return {
      ...incidentItem,
      alerts: [alertItem],
      topology: {
        nodes: [
          { id: 'gateway', kind: 'gateway', name: 'api-gateway' },
          { id: 'order-service', kind: 'service', name: 'order-service' },
        ],
        edges: [{ upstream: 'gateway', downstream: 'order-service', weight: 1 }],
      },
    }
  }
  if (/^\/api\/v1\/reports\/\d+$/.test(path)) {
    return reportItem
  }
  if (path === '/api/v1/evaluations') {
    return { items: [evaluationSummary], total: 1 }
  }
  if (/^\/api\/v1\/evaluations\/[^/]+$/.test(path)) {
    return {
      ...evaluationSummary,
      cases: [
        {
          scenario_id: 'db-pool-exhausted',
          expected: { root_cause_category: 'db_connection_pool_exhausted' },
          actual: { root_cause_category: 'db_connection_pool_exhausted' },
          top1_hit: true,
          top3_hit: true,
          node_hit: true,
          latency_ms: 10,
          error: null,
        },
      ],
    }
  }
  if (path === '/api/v1/scenarios') {
    return { items: [scenarioItem], total: 1 }
  }
  if (path === '/api/v1/topology') {
    return { nodes: [], edges: [] }
  }
  return {}
}

async function settle(rounds = 8): Promise<void> {
  for (let i = 0; i < rounds; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 10))
  }
}

describe('SPA 页面冒烟', () => {
  beforeEach(() => {
    Object.defineProperty(window, 'scrollTo', { value: () => {}, writable: true })
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = typeof input === 'string' ? input : input.toString()
        return new Response(JSON.stringify(responseFor(url)), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        })
      }),
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('可依次渲染概览/告警/事件/详情/报告/评估/模拟页', async () => {
    await router.push('/')
    await router.isReady()

    const wrapper = mount(App, { global: { plugins: [createPinia(), router] } })
    await settle()

    expect(wrapper.text()).toContain('运行概览')
    expect(wrapper.text()).toContain('告警总数')
    expect(wrapper.text()).toContain('严重级别分布')
    expect(wrapper.text()).toContain('Top 主机降噪')

    await router.push('/alerts')
    await settle()
    expect(wrapper.text()).toContain('告警列表')
    expect(wrapper.text()).toContain('order-host-01')

    await router.push('/incidents')
    await settle()
    expect(wrapper.text()).toContain('故障事件')
    expect(wrapper.text()).toContain('order-service')

    await router.push('/incidents/1')
    await settle()
    expect(wrapper.text()).toContain('事件摘要')
    expect(wrapper.text()).toContain('拓扑关系图')
    expect(wrapper.text()).toContain('诊断面板')
    expect(wrapper.text()).toContain('cpu_overload')

    await router.push('/reports/1')
    await settle()
    expect(wrapper.text()).toContain('报告概要')
    expect(wrapper.text()).toContain('订单服务 CPU 过载')
    expect(wrapper.text()).toContain('分步排查与修复')

    await router.push('/evaluations')
    await settle()
    expect(wrapper.text()).toContain('效果评估')
    expect(wrapper.text()).toContain('Top-1 准确率')

    await router.push('/simulate')
    await settle()
    expect(wrapper.text()).toContain('模拟注入')
    expect(wrapper.text()).toContain('连接池耗尽')

    wrapper.unmount()
  })
})

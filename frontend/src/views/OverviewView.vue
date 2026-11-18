<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'

import type { EChartsOption } from 'echarts'

import { describeError } from '@/api/client'
import { api } from '@/api/endpoints'
import EChart from '@/components/EChart.vue'
import { useLiveQuery } from '@/composables/useLiveQuery'
import {
  formatNumber,
  formatPercent,
  formatRatio,
  formatTime,
  relativeTime,
  severityLabel,
} from '@/utils/format'

const overview = useLiveQuery((signal) => api.statsOverview(signal), { interval: 3000 })
const noise = useLiveQuery((signal) => api.statsNoise(signal), { interval: 3000 })
const compression = useLiveQuery((signal) => api.statsCompression(signal), { interval: 3000 })

const overviewError = computed(() =>
  overview.error.value ? describeError(overview.error.value) : null,
)
const noiseError = computed(() => (noise.error.value ? describeError(noise.error.value) : null))

const stats = computed(() => overview.data.value)
const noiseStats = computed(() => noise.data.value)

const severityOrder = ['critical', 'high', 'medium', 'low', 'info'] as const
const severityColors: Record<string, string> = {
  critical: '#f87171',
  high: '#fbbf24',
  medium: '#a78bfa',
  low: '#34d399',
  info: '#38bdf8',
}

const severityItems = computed(() => {
  const dist = stats.value?.severity_distribution ?? {}
  return severityOrder
    .map((key) => ({
      key,
      label: severityLabel(key),
      count: Number(dist[key] ?? 0),
      color: severityColors[key] ?? '#94a3b8',
    }))
    .filter((item) => item.count > 0)
})

const severityChartOption = computed<EChartsOption>(() => ({
  tooltip: {
    trigger: 'item',
    formatter: '{b}: {c} 条（{d}%）',
  },
  legend: {
    orient: 'vertical',
    right: 8,
    top: 'center',
    textStyle: { color: '#94a3b8' },
    itemWidth: 10,
    itemHeight: 10,
  },
  series: [
    {
      name: '严重级别分布',
      type: 'pie',
      radius: ['42%', '70%'],
      center: ['36%', '50%'],
      avoidLabelOverlap: true,
      itemStyle: {
        borderColor: '#152238',
        borderWidth: 2,
        borderRadius: 4,
      },
      label: { color: '#cbd5e1', formatter: '{b}\n{c}' },
      labelLine: { lineStyle: { color: '#475569' } },
      data: severityItems.value.map((item) => ({
        name: item.label,
        value: item.count,
        itemStyle: { color: item.color },
      })),
    },
  ],
}))

const topHosts = computed(() => noiseStats.value?.top_hosts ?? [])

const topHostsChartOption = computed<EChartsOption>(() => {
  const items = [...topHosts.value].sort((a, b) => a.raw_count - b.raw_count).slice(-10)
  return {
    grid: { left: 12, right: 42, top: 12, bottom: 12, containLabel: true },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: {
      type: 'value',
      axisLine: { lineStyle: { color: '#334155' } },
      splitLine: { lineStyle: { color: '#1e293b' } },
      axisLabel: { color: '#94a3b8' },
    },
    yAxis: {
      type: 'category',
      data: items.map((item) => item.host),
      axisLine: { lineStyle: { color: '#334155' } },
      axisLabel: { color: '#cbd5e1', width: 150, overflow: 'truncate' },
    },
    series: [
      {
        name: '原始告警数',
        type: 'bar',
        data: items.map((item) => item.raw_count),
        barMaxWidth: 18,
        itemStyle: { color: '#38bdf8', borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: 'right', color: '#94a3b8' },
      },
    ],
  }
})

const topRuleFamilies = computed(() => noiseStats.value?.top_rule_families ?? [])

const accuracyValue = computed(() => {
  const value = stats.value
  if (!value) return '—'
  if (value.evaluation_id === null && !value.diagnosis_accuracy) return '—'
  return formatPercent(value.diagnosis_accuracy)
})

const accuracyFoot = computed(() => {
  if (!stats.value) return '等待统计接口'
  if (stats.value.evaluation_id === null) return '尚无评估结果，可前往评估页运行'
  return `来源评估：${stats.value.evaluation_id}`
})

function retryAll(): void {
  void overview.refresh()
  void noise.refresh()
  void compression.refresh()
}
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>运行概览</h1>
        <div class="page-sub">
          三维降噪 + 拓扑关联 + 根因诊断的实时指标
          <template v-if="stats">
            · 统计窗口 {{ formatTime(stats.window.start) }} ~ {{ formatTime(stats.window.end) }}
          </template>
        </div>
      </div>
      <div class="page-actions">
        <span class="faint small">
          <template v-if="overview.lastUpdated.value">
            更新于 {{ relativeTime(overview.lastUpdated.value) }} · 每 3 秒自动刷新
          </template>
        </span>
        <button class="btn sm" :disabled="overview.refreshing.value" @click="retryAll">刷新</button>
      </div>
    </div>

    <div v-if="overview.loading.value && !stats" class="panel">
      <div class="state-block">
        <span class="spinner"></span>
        <div class="state-desc">正在加载概览指标…</div>
      </div>
    </div>

    <div v-else-if="overviewError && !stats" class="panel">
      <div class="state-block error">
        <div class="state-title">概览加载失败</div>
        <div class="state-desc">{{ overviewError }}</div>
        <button class="btn sm" @click="retryAll">重试</button>
      </div>
    </div>

    <template v-else-if="stats">
      <div v-if="overviewError" class="inline-alert warn mb-16">
        <span>概览刷新失败：{{ overviewError }}</span>
        <span class="spacer"></span>
        <button class="btn sm" @click="retryAll">重试</button>
      </div>

      <div class="grid cols-5 mb-16">
        <div class="stat-card">
          <div class="stat-label">告警总数</div>
          <div class="stat-value">{{ formatNumber(stats.alert_count) }}</div>
          <div class="stat-foot">窗口内原始告警</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">事件数</div>
          <div class="stat-value">{{ formatNumber(stats.incident_count) }}</div>
          <div class="stat-foot">拓扑关联后的故障事件</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">压缩比</div>
          <div class="stat-value">{{ formatRatio(stats.compression_ratio) }}</div>
          <div class="stat-foot">
            <template v-if="compression.data.value">
              拓扑命中率 {{ formatPercent(compression.data.value.topology_hit_rate) }}
            </template>
            <template v-else>告警 / 事件</template>
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-label">拓扑命中率</div>
          <div class="stat-value">{{ formatPercent(stats.topology_hit_rate) }}</div>
          <div class="stat-foot">事件被拓扑成功关联的比例</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">诊断准确率</div>
          <div class="stat-value">{{ accuracyValue }}</div>
          <div class="stat-foot">{{ accuracyFoot }}</div>
        </div>
      </div>

      <div class="grid cols-2">
        <section class="panel">
          <div class="panel-head">
            <h2>严重级别分布</h2>
            <span class="panel-sub">窗口内全部告警</span>
          </div>
          <div class="panel-body">
            <div v-if="severityItems.length === 0" class="chart-empty">暂无告警数据</div>
            <EChart v-else :option="severityChartOption" height="300px" />
          </div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <h2>Top 主机降噪</h2>
            <span class="panel-sub">原始告警最多的主机（/stats/noise）</span>
          </div>
          <div class="panel-body">
            <div v-if="noiseError" class="inline-alert warn mb-16">
              <span>{{ noiseError }}</span>
              <span class="spacer"></span>
              <button class="btn sm" @click="noise.refresh()">重试</button>
            </div>
            <div v-if="topHosts.length === 0" class="chart-empty">暂无降噪数据</div>
            <EChart v-else :option="topHostsChartOption" height="300px" />
          </div>
        </section>
      </div>

      <div class="grid cols-2 mt-16">
        <section class="panel">
          <div class="panel-head">
            <h2>Top 规则族</h2>
            <span class="panel-sub">按规则族统计的原始告警</span>
          </div>
          <div class="panel-body">
            <div v-if="topRuleFamilies.length === 0" class="chart-empty">暂无数据</div>
            <div v-else class="table-wrap">
              <table class="table">
                <thead>
                  <tr>
                    <th>规则族</th>
                    <th class="num">原始告警数</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in topRuleFamilies" :key="item.rule_family">
                    <td class="mono">{{ item.rule_family }}</td>
                    <td class="num">{{ formatNumber(item.raw_count) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <h2>降噪摘要</h2>
            <span class="panel-sub">{{ noiseStats ? formatTime(noiseStats.window.start) + ' ~ ' + formatTime(noiseStats.window.end) : '—' }}</span>
          </div>
          <div class="panel-body">
            <div v-if="!noiseStats" class="chart-empty">暂无数据</div>
            <div v-else class="kv-grid">
              <div class="kv">
                <div class="kv-label">原始告警</div>
                <div class="kv-value">{{ formatNumber(noiseStats.raw_alert_count) }}</div>
              </div>
              <div class="kv">
                <div class="kv-label">聚合告警</div>
                <div class="kv-value">{{ formatNumber(noiseStats.cluster_count) }}</div>
              </div>
              <div class="kv">
                <div class="kv-label">压缩比</div>
                <div class="kv-value">{{ formatRatio(noiseStats.compression_ratio) }}</div>
              </div>
            </div>
            <div class="row mt-16">
              <RouterLink class="btn sm" to="/alerts">查看告警列表</RouterLink>
              <RouterLink class="btn sm" to="/incidents">查看故障事件</RouterLink>
              <RouterLink class="btn sm" to="/evaluations">运行效果评估</RouterLink>
            </div>
          </div>
        </section>
      </div>
    </template>

    <div v-else class="panel">
      <div class="state-block error">
        <div class="state-title">概览不可用</div>
        <div class="state-desc">{{ overviewError ?? '暂无统计结果' }}</div>
        <button class="btn sm" @click="retryAll">重试</button>
      </div>
    </div>
  </div>
</template>

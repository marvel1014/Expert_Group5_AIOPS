<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { describeError } from '@/api/client'
import type { Incident } from '@/api/types'
import { api } from '@/api/endpoints'
import DataState from '@/components/DataState.vue'
import Pagination from '@/components/Pagination.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { useLiveQuery } from '@/composables/useLiveQuery'
import { useFilterStore } from '@/stores/filters'
import {
  INCIDENT_STATUS_OPTIONS,
  SEVERITY_OPTIONS,
  formatNumber,
  formatTime,
  statusLabel,
  statusTone,
} from '@/utils/format'

const filters = useFilterStore()

const draft = reactive({
  status: filters.incidents.status,
  severity: filters.incidents.severity,
})

const query = computed(() => ({
  status: filters.incidents.status || undefined,
  severity: filters.incidents.severity || undefined,
  page: filters.incidents.page,
  page_size: filters.incidents.page_size,
}))

const { data, error, loading, refreshing, lastUpdated, refresh } = useLiveQuery(
  (signal) => api.listIncidents(query.value, signal),
  { interval: 3000 },
)

watch(
  () => JSON.stringify(filters.incidents),
  () => {
    void refresh()
  },
)

const errorText = computed(() => (error.value ? describeError(error.value) : null))
const isEmpty = computed(
  () => !loading.value && !errorText.value && (data.value?.items.length ?? 0) === 0,
)
const hasActiveFilter = computed(
  () => Boolean(filters.incidents.status) || Boolean(filters.incidents.severity),
)

/**
 * 事件列表契约中没有"诊断状态"字段，这里对当前页事件按需聚合
 * GET /incidents/{id}/diagnoses 的最近一次状态，15 秒节流避免轮询放大。
 */
const diagnosisStates = ref<Record<number, string>>({})
const diagnosisLoading = ref<Record<number, boolean>>({})
let lastDiagnosisFetch = 0

function latestDiagnosisState(states: string[]): string {
  if (states.includes('running')) return 'running'
  if (states.includes('pending')) return 'pending'
  if (states.includes('succeeded')) return 'succeeded'
  if (states.includes('failed')) return 'failed'
  if (states.includes('degraded')) return 'degraded'
  return states[0] ?? 'none'
}

async function loadDiagnosisStates(incidents: Incident[]): Promise<void> {
  if (Date.now() - lastDiagnosisFetch < 15_000) return
  lastDiagnosisFetch = Date.now()
  await Promise.all(
    incidents.map(async (incident) => {
      diagnosisLoading.value = { ...diagnosisLoading.value, [incident.id]: true }
      try {
        const result = await api.listDiagnoses(incident.id)
        diagnosisStates.value = {
          ...diagnosisStates.value,
          [incident.id]: latestDiagnosisState(result.items.map((item) => item.status)),
        }
      } catch {
        diagnosisStates.value = { ...diagnosisStates.value, [incident.id]: 'error' }
      } finally {
        const next = { ...diagnosisLoading.value }
        delete next[incident.id]
        diagnosisLoading.value = next
      }
    }),
  )
}

watch(
  () => (data.value?.items ?? []).map((item) => item.id).join(','),
  () => {
    const items = data.value?.items ?? []
    if (items.length) void loadDiagnosisStates(items)
  },
)

function applyFilters(): void {
  filters.incidents.status = draft.status
  filters.incidents.severity = draft.severity
  filters.incidents.page = 1
  void refresh()
}

function resetFilters(): void {
  filters.resetIncidents()
  draft.status = ''
  draft.severity = ''
  void refresh()
}

function onPageChange(page: number): void {
  filters.incidents.page = page
}

function onPageSizeChange(pageSize: number): void {
  filters.incidents.page_size = pageSize
  filters.incidents.page = 1
}

function diagnosisLabel(incidentId: number): string {
  const state = diagnosisStates.value[incidentId]
  if (!state) return diagnosisLoading.value[incidentId] ? '加载中' : '未诊断'
  return statusLabel(state)
}

function diagnosisBadgeTone(incidentId: number): string {
  const state = diagnosisStates.value[incidentId]
  if (!state || state === 'none') return 'muted'
  return statusTone(state)
}
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>故障事件</h1>
        <div class="page-sub">
          拓扑关联后的聚合事件，点击进入详情可查看成员告警、拓扑与诊断结论
          <template v-if="data"> · 共 {{ formatNumber(data.total) }} 个事件</template>
        </div>
      </div>
      <div class="page-actions">
        <span class="faint small">
          <template v-if="lastUpdated">更新于 {{ formatTime(lastUpdated.toISOString()) }} · 3 秒轮询</template>
          <span v-if="refreshing" class="spinner sm"></span>
        </span>
        <button class="btn sm" :disabled="refreshing" @click="refresh()">刷新</button>
      </div>
    </div>

    <section class="panel mb-16">
      <div class="panel-body">
        <form class="filters" @submit.prevent="applyFilters">
          <div class="field">
            <label for="incident-status">状态</label>
            <select id="incident-status" v-model="draft.status" class="select">
              <option value="">全部</option>
              <option v-for="item in INCIDENT_STATUS_OPTIONS" :key="item.value" :value="item.value">
                {{ item.label }}
              </option>
            </select>
          </div>
          <div class="field">
            <label for="incident-severity">严重级别</label>
            <select id="incident-severity" v-model="draft.severity" class="select">
              <option value="">全部</option>
              <option v-for="item in SEVERITY_OPTIONS" :key="item.value" :value="item.value">
                {{ item.label }}（{{ item.value }}）
              </option>
            </select>
          </div>
          <div class="row">
            <button class="btn primary" type="submit">查询</button>
            <button class="btn" type="button" @click="resetFilters">重置</button>
          </div>
        </form>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2>事件列表</h2>
        <span class="panel-sub">诊断状态由各事件最近一次诊断聚合展示（15 秒节流）</span>
      </div>

      <DataState
        :loading="loading"
        :error="errorText"
        :empty="isEmpty"
        :has-data="Boolean(data)"
        :empty-text="hasActiveFilter ? '当前筛选条件没有匹配的事件，可调整或重置筛选条件。' : '暂无故障事件，可在模拟注入页注入故障场景生成事件。'"
        loading-text="正在加载事件列表…"
        @retry="refresh()"
      >
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>ID</th>
                <th>标题</th>
                <th>严重级别</th>
                <th>状态</th>
                <th>诊断</th>
                <th class="num">告警数</th>
                <th>涉及节点</th>
                <th>拓扑</th>
                <th>窗口开始</th>
                <th>窗口结束</th>
                <th>详情</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="incident in data?.items ?? []" :key="incident.id">
                <td class="mono">{{ incident.id }}</td>
                <td style="max-width: 360px">
                  <RouterLink :to="`/incidents/${incident.id}`">{{ incident.title }}</RouterLink>
                </td>
                <td><StatusBadge :value="incident.severity" kind="severity" /></td>
                <td><StatusBadge :value="incident.status" kind="status" /></td>
                <td>
                  <span class="badge" :class="`tone-${diagnosisBadgeTone(incident.id)}`">
                    {{ diagnosisLabel(incident.id) }}
                  </span>
                </td>
                <td class="num">{{ incident.alert_count }}</td>
                <td>
                  <span v-for="node in incident.nodes.slice(0, 3)" :key="node" class="chip">{{ node }}</span>
                  <span v-if="incident.nodes.length > 3" class="chip">+{{ incident.nodes.length - 3 }}</span>
                </td>
                <td>
                  <span class="badge" :class="incident.topology_linked ? 'tone-ok' : 'tone-muted'">
                    {{ incident.topology_linked ? '已关联' : '未关联' }}
                  </span>
                </td>
                <td class="nowrap">{{ formatTime(incident.window_start) }}</td>
                <td class="nowrap">{{ formatTime(incident.window_end) }}</td>
                <td><RouterLink class="btn sm" :to="`/incidents/${incident.id}`">查看</RouterLink></td>
              </tr>
            </tbody>
          </table>
        </div>
      </DataState>

      <template v-if="data && !isEmpty">
        <Pagination
          :page="data.page"
          :page-size="data.page_size"
          :total="data.total"
          @update:page="onPageChange"
          @update:page-size="onPageSizeChange"
        />
      </template>
    </section>
  </div>
</template>

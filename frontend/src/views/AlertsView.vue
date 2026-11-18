<script setup lang="ts">
import { computed, reactive, watch } from 'vue'

import { describeError } from '@/api/client'
import type { Alert } from '@/api/types'
import { api } from '@/api/endpoints'
import DataState from '@/components/DataState.vue'
import Pagination from '@/components/Pagination.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { useLiveQuery } from '@/composables/useLiveQuery'
import { useFilterStore } from '@/stores/filters'
import {
  ALERT_STATUS_OPTIONS,
  SEVERITY_OPTIONS,
  formatNumber,
  formatTime,
  severityRank,
} from '@/utils/format'

const filters = useFilterStore()

const draft = reactive({
  host: filters.alerts.host,
  service: filters.alerts.service,
  severity: filters.alerts.severity,
  status: filters.alerts.status,
  sort: filters.alerts.sort,
})

const query = computed(() => ({
  host: filters.alerts.host || undefined,
  service: filters.alerts.service || undefined,
  severity: filters.alerts.severity || undefined,
  status: filters.alerts.status || undefined,
  page: filters.alerts.page,
  page_size: filters.alerts.page_size,
}))

const { data, error, loading, refreshing, lastUpdated, refresh } = useLiveQuery(
  (signal) => api.listAlerts(query.value, signal),
  { interval: 3000 },
)

watch(
  () => JSON.stringify(filters.alerts),
  () => {
    void refresh()
  },
)

const errorText = computed(() => (error.value ? describeError(error.value) : null))

const sortedItems = computed<Alert[]>(() => {
  const items = [...(data.value?.items ?? [])]
  switch (filters.alerts.sort) {
    case 'occurred_at_asc':
      return items.sort(
        (a, b) => new Date(a.occurred_at).getTime() - new Date(b.occurred_at).getTime(),
      )
    case 'severity_desc':
      return items.sort(
        (a, b) => severityRank(a.severity) - severityRank(b.severity) ||
          new Date(b.occurred_at).getTime() - new Date(a.occurred_at).getTime(),
      )
    case 'repeat_desc':
      return items.sort((a, b) => b.repeat_count - a.repeat_count)
    default:
      return items.sort(
        (a, b) => new Date(b.occurred_at).getTime() - new Date(a.occurred_at).getTime(),
      )
  }
})

const isEmpty = computed(
  () => !loading.value && !errorText.value && (data.value?.items.length ?? 0) === 0,
)

const hasActiveFilter = computed(
  () =>
    Boolean(filters.alerts.host) ||
    Boolean(filters.alerts.service) ||
    Boolean(filters.alerts.severity) ||
    Boolean(filters.alerts.status),
)

function applyFilters(): void {
  filters.alerts.host = draft.host.trim()
  filters.alerts.service = draft.service.trim()
  filters.alerts.severity = draft.severity
  filters.alerts.status = draft.status
  filters.alerts.sort = draft.sort
  filters.alerts.page = 1
  void refresh()
}

function resetFilters(): void {
  filters.resetAlerts()
  draft.host = ''
  draft.service = ''
  draft.severity = ''
  draft.status = ''
  draft.sort = 'occurred_at_desc'
  void refresh()
}

function onPageChange(page: number): void {
  filters.alerts.page = page
}

function onPageSizeChange(pageSize: number): void {
  filters.alerts.page_size = pageSize
  filters.alerts.page = 1
}

function clearEmptyAction(): void {
  if (hasActiveFilter.value) resetFilters()
  else void refresh()
}
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>告警列表</h1>
        <div class="page-sub">
          原始告警明细，支持按主机、服务、严重级别与状态筛选
          <template v-if="data">
            · 共 {{ formatNumber(data.total) }} 条
          </template>
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
            <label for="alert-host">主机</label>
            <input id="alert-host" v-model="draft.host" class="input" placeholder="如 db-host-01" />
          </div>
          <div class="field">
            <label for="alert-service">服务</label>
            <input id="alert-service" v-model="draft.service" class="input" placeholder="如 mysql-primary" />
          </div>
          <div class="field">
            <label for="alert-severity">严重级别</label>
            <select id="alert-severity" v-model="draft.severity" class="select">
              <option value="">全部</option>
              <option v-for="item in SEVERITY_OPTIONS" :key="item.value" :value="item.value">
                {{ item.label }}（{{ item.value }}）
              </option>
            </select>
          </div>
          <div class="field">
            <label for="alert-status">状态</label>
            <select id="alert-status" v-model="draft.status" class="select">
              <option value="">全部</option>
              <option v-for="item in ALERT_STATUS_OPTIONS" :key="item.value" :value="item.value">
                {{ item.label }}
              </option>
            </select>
          </div>
          <div class="field">
            <label for="alert-sort">排序（当前页）</label>
            <select id="alert-sort" v-model="draft.sort" class="select">
              <option value="occurred_at_desc">最近发生优先</option>
              <option value="occurred_at_asc">最早发生优先</option>
              <option value="severity_desc">严重级别优先</option>
              <option value="repeat_desc">重复次数优先</option>
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
        <h2>告警明细</h2>
        <span class="panel-sub">按时间倒序分页展示原始告警</span>
      </div>

      <DataState
        :loading="loading"
        :error="errorText"
        :empty="isEmpty"
        :has-data="Boolean(data)"
        :empty-text="hasActiveFilter ? '当前筛选条件没有匹配的告警，可调整或重置筛选条件。' : '暂无告警数据，可在模拟注入页注入一条故障场景。'"
        loading-text="正在加载告警列表…"
        @retry="refresh()"
      >
        <template #empty-action>
          <button class="btn sm" @click="clearEmptyAction">
            {{ hasActiveFilter ? '重置筛选' : '重新加载' }}
          </button>
        </template>
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>ID</th>
                <th>严重级别</th>
                <th>状态</th>
                <th>主机</th>
                <th>服务</th>
                <th>规则 / 消息</th>
                <th>首次出现</th>
                <th>最后出现</th>
                <th class="num">重复</th>
                <th class="num">聚合</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="alert in sortedItems" :key="alert.id">
                <td class="mono">{{ alert.id }}</td>
                <td><StatusBadge :value="alert.severity" kind="severity" /></td>
                <td><StatusBadge :value="alert.status" kind="status" /></td>
                <td class="mono">{{ alert.host }}</td>
                <td class="mono">{{ alert.service }}</td>
                <td style="max-width: 340px">
                  <div>{{ alert.rule }}</div>
                  <div class="faint small">{{ alert.message }}</div>
                  <div v-if="alert.rule_family" class="faint small mono">
                    rule_family: {{ alert.rule_family }}
                  </div>
                </td>
                <td class="nowrap">{{ formatTime(alert.first_seen) }}</td>
                <td class="nowrap">{{ formatTime(alert.last_seen) }}</td>
                <td class="num">{{ alert.repeat_count }}</td>
                <td class="num">{{ alert.cluster_id ?? '—' }}</td>
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

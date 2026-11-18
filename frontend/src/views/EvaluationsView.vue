<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { describeError } from '@/api/client'
import type { EvaluationCase } from '@/api/types'
import { api } from '@/api/endpoints'
import DataState from '@/components/DataState.vue'
import { useLiveQuery } from '@/composables/useLiveQuery'
import {
  formatDuration,
  formatNumber,
  formatPercent,
  formatRatio,
  formatTime,
  statusLabel,
  statusTone,
} from '@/utils/format'

const scenarios = useLiveQuery((signal) => api.listScenarios(signal), { interval: 30_000 })
const evaluations = useLiveQuery((signal) => api.listEvaluations(signal), { interval: 3000 })

const selectedScenarios = ref<string[]>([])
const seed = ref('42')
const starting = ref(false)
const startError = ref<string | null>(null)
const startMessage = ref<string | null>(null)

const selectedId = ref<string | null>(null)

const detail = useLiveQuery(
  (signal) => {
    if (!selectedId.value) return Promise.resolve(null)
    return api.getEvaluation(selectedId.value, signal)
  },
  { interval: 2000 },
)

const evaluationDetail = computed(() => detail.data.value)

watch(
  () => evaluations.data.value?.items,
  (items) => {
    if (!items?.length) return
    if (selectedId.value === null) selectedId.value = items[0]?.id ?? null
  },
  { immediate: true },
)

// 新完成的评估自动跟随（例如刚提交的任务）
watch(
  () => evaluations.data.value?.items?.[0]?.id,
  (latestId) => {
    if (!latestId) return
    if (selectedId.value === null) selectedId.value = latestId
  },
)

// 自动选中评估后立即拉取详情
watch(selectedId, () => {
  void detail.refresh()
})

const listError = computed(() =>
  evaluations.error.value ? describeError(evaluations.error.value) : null,
)
const scenariosError = computed(() =>
  scenarios.error.value ? describeError(scenarios.error.value) : null,
)
const detailError = computed(() => (detail.error.value ? describeError(detail.error.value) : null))

const caseRows = computed(() => evaluationDetail.value?.cases ?? [])

const metrics = computed(() => evaluationDetail.value?.metrics ?? null)

function toggleScenario(id: string): void {
  const index = selectedScenarios.value.indexOf(id)
  if (index >= 0) selectedScenarios.value.splice(index, 1)
  else selectedScenarios.value.push(id)
}

function selectAllScenarios(): void {
  selectedScenarios.value = (scenarios.data.value?.items ?? []).map((item) => item.id)
}

function clearScenarios(): void {
  selectedScenarios.value = []
}

async function startEvaluation(): Promise<void> {
  starting.value = true
  startError.value = null
  startMessage.value = null
  try {
    const body: { scenario_ids?: string[]; seed?: number } = {}
    if (selectedScenarios.value.length) body.scenario_ids = [...selectedScenarios.value]
    const seedNumber = Number(seed.value)
    if (Number.isFinite(seedNumber)) body.seed = seedNumber
    const result = await api.startEvaluation(body)
    selectedId.value = result.id
    startMessage.value = `评估任务已提交：${result.id}（状态 ${statusLabel(result.status)}）`
    await evaluations.refresh()
    await detail.refresh()
  } catch (error) {
    startError.value = describeError(error)
  } finally {
    starting.value = false
  }
}

function selectEvaluation(id: string): void {
  selectedId.value = id
  void detail.refresh()
}

function expectedCategory(item: EvaluationCase): string {
  const value = item.expected?.root_cause_category
  return typeof value === 'string' && value ? value : '—'
}

function expectedNode(item: EvaluationCase): string {
  const value = item.expected?.root_cause_node
  return typeof value === 'string' && value ? value : '—'
}

function pick(record: Record<string, unknown> | null | undefined, keys: string[]): string {
  if (!record) return '—'
  for (const key of keys) {
    const value = record[key]
    if (typeof value === 'string' && value) return value
  }
  return '—'
}

function actualCategory(item: EvaluationCase): string {
  return pick(item.actual, ['root_cause_category', 'category', 'root_cause'])
}

function actualNode(item: EvaluationCase): string {
  return pick(item.actual, ['root_cause_node', 'node'])
}

function actualRaw(item: EvaluationCase): string {
  try {
    return JSON.stringify(item.actual, null, 2)
  } catch {
    return String(item.actual)
  }
}

function hitBadge(hit: boolean): string {
  return hit ? 'tone-ok' : 'tone-danger'
}

function evaluationStatusTone(status: string | undefined): string {
  return statusTone(status)
}
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>效果评估</h1>
        <div class="page-sub">
          在固定种子与 Mock 模式下回放内置故障场景，度量 Top-1/Top-3 类别命中与根因节点命中
        </div>
      </div>
      <div class="page-actions">
        <span v-if="evaluations.lastUpdated.value" class="faint small">
          更新于 {{ formatTime(evaluations.lastUpdated.value.toISOString()) }}
          <span v-if="evaluations.refreshing.value" class="spinner sm"></span>
        </span>
        <button class="btn sm" :disabled="evaluations.refreshing.value" @click="evaluations.refresh()">
          刷新列表
        </button>
      </div>
    </div>

    <section class="panel mb-16">
      <div class="panel-head">
        <h2>触发评估</h2>
        <span class="panel-sub">不选场景则评估全部内置场景</span>
        <div class="panel-actions">
          <button class="btn sm" @click="selectAllScenarios">全选</button>
          <button class="btn sm" @click="clearScenarios">清空</button>
        </div>
      </div>
      <div class="panel-body">
        <div v-if="scenariosError" class="inline-alert warn mb-16">
          <span>场景库加载失败：{{ scenariosError }}</span>
          <span class="spacer"></span>
          <button class="btn sm" @click="scenarios.refresh()">重试</button>
        </div>
        <div v-if="scenarios.loading.value && !scenarios.data.value" class="state-block">
          <span class="spinner"></span>
          <div class="state-desc">正在加载故障场景库…</div>
        </div>
        <div v-else-if="(scenarios.data.value?.items.length ?? 0) === 0" class="state-block">
          <div class="state-title">场景库为空</div>
          <div class="state-desc">未加载到内置场景，请检查后端 scenarios 目录或运行 make seed。</div>
        </div>
        <template v-else>
          <div class="section-title">
            选择场景（已选 {{ selectedScenarios.length }} / {{ scenarios.data.value?.items.length ?? 0 }}）
          </div>
          <div class="grid cols-3 mb-16">
            <label
              v-for="scenario in scenarios.data.value?.items ?? []"
              :key="scenario.id"
              class="kv"
              style="cursor: pointer"
            >
              <div class="row">
                <input
                  type="checkbox"
                  :checked="selectedScenarios.includes(scenario.id)"
                  @change="toggleScenario(scenario.id)"
                />
                <strong>{{ scenario.name }}</strong>
              </div>
              <div class="faint small mono mt-8">{{ scenario.id }}</div>
              <div class="faint small mt-8">{{ scenario.description }}</div>
              <div class="faint small mt-8">
                告警 {{ scenario.alert_count }} 条 · 日志 {{ scenario.log_count }} 条 · 类别
                {{ scenario.category }}
              </div>
            </label>
          </div>

          <form class="filters" @submit.prevent="startEvaluation">
            <div class="field">
              <label for="eval-seed">随机种子</label>
              <input id="eval-seed" v-model="seed" class="input" inputmode="numeric" />
            </div>
            <button class="btn primary" type="submit" :disabled="starting">
              {{ starting ? '提交中…' : '开始评估' }}
            </button>
            <span class="faint small">评估在后台执行，完成后自动刷新结果</span>
          </form>

          <div v-if="startError" class="inline-alert warn mt-16">
            <span>{{ startError }}</span>
          </div>
          <div v-if="startMessage" class="inline-alert mt-16" style="border-color: rgba(52,211,153,.35); background: var(--ok-soft); color: #a7f3d0">
            <span>{{ startMessage }}</span>
          </div>
        </template>
      </div>
    </section>

    <div class="grid cols-2 mb-16" style="grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.6fr)">
      <section class="panel">
        <div class="panel-head">
          <h2>评估列表</h2>
          <span class="panel-sub">每 3 秒刷新</span>
        </div>
        <DataState
          :loading="evaluations.loading.value"
          :error="listError"
          :empty="(evaluations.data.value?.items.length ?? 0) === 0 && !listError"
          empty-text="暂无评估记录，可在上方选择场景后开始评估。"
          :has-data="Boolean(evaluations.data.value)"
          loading-text="正在加载评估列表…"
          @retry="evaluations.refresh()"
        >
          <div class="table-wrap">
            <table class="table">
              <thead>
                <tr>
                  <th>评估 ID</th>
                  <th>状态</th>
                  <th class="num">Top-1</th>
                  <th>创建时间</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="item in evaluations.data.value?.items ?? []"
                  :key="item.id"
                  style="cursor: pointer"
                  @click="selectEvaluation(item.id)"
                >
                  <td class="mono" style="max-width: 180px; word-break: break-all">
                    {{ item.id }}
                    <span v-if="item.id === selectedId" class="badge tone-info">当前</span>
                  </td>
                  <td>
                    <span class="badge" :class="`tone-${evaluationStatusTone(item.status)}`">
                      {{ statusLabel(item.status) }}
                    </span>
                  </td>
                  <td class="num">{{ formatPercent(item.metrics?.top1_accuracy) }}</td>
                  <td class="nowrap">{{ formatTime(item.created_at) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </DataState>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>评估详情</h2>
          <span v-if="evaluationDetail" class="panel-sub mono">{{ evaluationDetail.id }}</span>
          <div class="panel-actions">
            <a
              v-if="evaluationDetail"
              class="btn sm"
              :href="api.evaluationExportUrl(evaluationDetail.id)"
              target="_blank"
              rel="noopener"
            >
              导出 JSON
            </a>
            <button
              v-if="selectedId"
              class="btn sm"
              :disabled="detail.refreshing.value"
              @click="detail.refresh()"
            >
              刷新
            </button>
          </div>
        </div>
        <div class="panel-body">
          <div v-if="detailError" class="inline-alert warn mb-16">
            <span>评估详情加载失败：{{ detailError }}</span>
            <span class="spacer"></span>
            <button class="btn sm" @click="detail.refresh()">重试</button>
          </div>

          <div v-if="detail.loading.value && !evaluationDetail" class="state-block">
            <span class="spinner"></span>
            <div class="state-desc">正在加载评估详情…</div>
          </div>

          <div v-else-if="!selectedId" class="state-block">
            <div class="state-title">未选择评估</div>
            <div class="state-desc">点击左侧评估列表中的任意一条查看指标与逐场景用例。</div>
          </div>

          <div v-else-if="!evaluationDetail" class="state-block">
            <div class="state-title">评估不可用</div>
            <div class="state-desc">未获取到评估详情，可能仍在后台执行，稍后自动刷新。</div>
          </div>

          <template v-else>
            <div class="row mb-16">
              <span class="badge" :class="`tone-${evaluationStatusTone(evaluationDetail.status)}`">
                {{ statusLabel(evaluationDetail.status) }}
              </span>
              <span class="faint small">创建于 {{ formatTime(evaluationDetail.created_at) }}</span>
              <span v-if="['pending', 'running'].includes(evaluationDetail.status)" class="spinner sm"></span>
            </div>

            <div v-if="metrics" class="kv-grid mb-16">
              <div class="kv">
                <div class="kv-label">用例总数</div>
                <div class="kv-value">{{ formatNumber(metrics.total) }}</div>
              </div>
              <div class="kv">
                <div class="kv-label">Top-1 准确率</div>
                <div class="kv-value">{{ formatPercent(metrics.top1_accuracy) }}</div>
              </div>
              <div class="kv">
                <div class="kv-label">Top-3 准确率</div>
                <div class="kv-value">{{ formatPercent(metrics.top3_accuracy) }}</div>
              </div>
              <div class="kv">
                <div class="kv-label">根因节点命中</div>
                <div class="kv-value">{{ formatPercent(metrics.node_accuracy) }}</div>
              </div>
              <div class="kv">
                <div class="kv-label">平均诊断耗时</div>
                <div class="kv-value">{{ formatDuration(metrics.avg_latency_ms) }}</div>
              </div>
              <div class="kv">
                <div class="kv-label">平均压缩比</div>
                <div class="kv-value">{{ formatRatio(metrics.avg_compression_ratio) }}</div>
              </div>
            </div>
            <div v-else class="muted small mb-16">评估尚未生成指标（状态 {{ statusLabel(evaluationDetail.status) }}）。</div>

            <div class="section-title">逐场景用例（{{ caseRows.length }}）</div>
            <div v-if="caseRows.length === 0" class="chart-empty">暂无用例结果</div>
            <div v-else class="table-wrap">
              <table class="table">
                <thead>
                  <tr>
                    <th>场景</th>
                    <th>期望类别 / 节点</th>
                    <th>实际类别 / 节点</th>
                    <th>Top-1</th>
                    <th>Top-3</th>
                    <th>节点</th>
                    <th class="num">耗时</th>
                    <th>错误</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="item in caseRows" :key="item.scenario_id">
                    <td class="mono">{{ item.scenario_id }}</td>
                    <td>
                      <div>{{ expectedCategory(item) }}</div>
                      <div class="faint small">{{ expectedNode(item) }}</div>
                    </td>
                    <td>
                      <div>{{ actualCategory(item) }}</div>
                      <div class="faint small">{{ actualNode(item) }}</div>
                      <details class="raw-block">
                        <summary>actual</summary>
                        <pre class="raw-pre">{{ actualRaw(item) }}</pre>
                      </details>
                    </td>
                    <td><span class="badge" :class="hitBadge(item.top1_hit)">{{ item.top1_hit ? '命中' : '未中' }}</span></td>
                    <td><span class="badge" :class="hitBadge(item.top3_hit)">{{ item.top3_hit ? '命中' : '未中' }}</span></td>
                    <td><span class="badge" :class="hitBadge(item.node_hit)">{{ item.node_hit ? '命中' : '未中' }}</span></td>
                    <td class="num">{{ formatDuration(item.latency_ms) }}</td>
                    <td style="max-width: 180px">
                      <span v-if="item.error" style="color: var(--danger)">{{ item.error }}</span>
                      <span v-else class="faint">—</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import type { EChartsOption } from 'echarts'

import { ApiError, describeError } from '@/api/client'
import type { Diagnosis, FastDiagnosis } from '@/api/types'
import { api } from '@/api/endpoints'
import EChart from '@/components/EChart.vue'
import StatusBadge from '@/components/StatusBadge.vue'
import { useLiveQuery } from '@/composables/useLiveQuery'
import {
  formatDuration,
  formatNumber,
  formatPercent,
  formatTime,
  statusLabel,
  statusTone,
} from '@/utils/format'
import { buildTopologyOption } from '@/utils/topology'
import type { RecentReport } from '@/utils/recentReports'
import { loadRecentReports, rememberReport } from '@/utils/recentReports'

const route = useRoute()
const router = useRouter()

const incidentId = computed(() => {
  const raw = route.params.id
  const parsed = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 0
})

const validId = computed(() => incidentId.value > 0)

const detail = useLiveQuery(
  (signal) => {
    if (!validId.value) {
      throw new ApiError(404, 'invalid_id', '无效的事件 ID')
    }
    return api.getIncident(incidentId.value, signal)
  },
  { interval: 3000 },
)

const diagnoses = useLiveQuery(
  (signal) => {
    if (!validId.value) {
      throw new ApiError(404, 'invalid_id', '无效的事件 ID')
    }
    return api.listDiagnoses(incidentId.value, signal)
  },
  { interval: 2000 },
)

const detailError = computed(() => (detail.error.value ? describeError(detail.error.value) : null))
const diagnosesError = computed(() =>
  diagnoses.error.value ? describeError(diagnoses.error.value) : null,
)

const incident = computed(() => detail.data.value)

const diagnosesSorted = computed<Diagnosis[]>(() => {
  const items = [...(diagnoses.data.value?.items ?? [])]
  return items.sort((a, b) => b.version - a.version || b.id - a.id)
})

const latestDiagnosis = computed<Diagnosis | null>(() => diagnosesSorted.value[0] ?? null)

const selectedId = ref<number | null>(null)

watch(
  () => diagnosesSorted.value,
  (items) => {
    if (items.length === 0) return
    if (selectedId.value === null) {
      selectedId.value = items[0]?.id ?? null
      return
    }
    const current = items.find((item) => item.id === selectedId.value)
    const latest = items[0]
    const active = current && ['pending', 'running'].includes(current.status)
    if (active && latest && !['pending', 'running'].includes(latest.status)) {
      selectedId.value = latest.id
    }
  },
  { immediate: true },
)

const selectedDiagnosis = computed<Diagnosis | null>(() => {
  const items = diagnosesSorted.value
  if (items.length === 0) return null
  return items.find((item) => item.id === selectedId.value) ?? items[0] ?? null
})

const diagnosisRunning = computed(() =>
  diagnosesSorted.value.some((item) => ['pending', 'running'].includes(item.status)),
)

const topologyOption = computed<EChartsOption | null>(() => {
  const topology = incident.value?.topology
  if (!topology || topology.nodes.length === 0) return null
  return buildTopologyOption(topology, incident.value?.nodes ?? []) as EChartsOption
})

const rootCandidates = computed(() => {
  const candidates = [...(incident.value?.root_candidates ?? [])]
  return candidates.sort((a, b) => b.score - a.score)
})

// -------------------------------------------------------------- 诊断与报告操作

const diagnoseError = ref<string | null>(null)
const diagnosing = ref(false)

async function startDiagnosis(rerun = false): Promise<void> {
  if (!validId.value) return
  diagnosing.value = true
  diagnoseError.value = null
  try {
    const result = await api.diagnoseIncident(incidentId.value, rerun)
    selectedId.value = result.diagnosis_id
    await diagnoses.refresh()
  } catch (error) {
    diagnoseError.value = describeError(error)
  } finally {
    diagnosing.value = false
  }
}

const reportError = ref<string | null>(null)
const reportBusy = ref(false)
const reports = ref<RecentReport[]>([])

function refreshRecentReports(): void {
  reports.value = loadRecentReports().filter((item) => item.incidentId === incidentId.value)
}

onMounted(refreshRecentReports)

async function generateReport(): Promise<void> {
  if (!validId.value) return
  reportBusy.value = true
  reportError.value = null
  try {
    const report = await api.createReport(incidentId.value, selectedDiagnosis.value?.id)
    rememberReport(report, incident.value?.title)
    refreshRecentReports()
    await router.push(`/reports/${report.id}`)
  } catch (error) {
    reportError.value = describeError(error)
  } finally {
    reportBusy.value = false
  }
}

const incidentActionError = ref<string | null>(null)
const incidentActionBusy = ref(false)

async function updateIncidentStatus(action: 'ack' | 'resolve'): Promise<void> {
  incidentActionBusy.value = true
  incidentActionError.value = null
  try {
    if (action === 'ack') await api.ackIncident(incidentId.value)
    else await api.resolveIncident(incidentId.value)
    await detail.refresh()
  } catch (error) {
    incidentActionError.value = describeError(error)
  } finally {
    incidentActionBusy.value = false
  }
}

// ------------------------------------------------------------------ 快诊（决策模型）

const fastDiagnosis = ref<FastDiagnosis | null>(null)
const fastDiagnosisError = ref<string | null>(null)
const fastDiagnosisBusy = ref(false)

async function loadFastDiagnosis(): Promise<void> {
  fastDiagnosisBusy.value = true
  fastDiagnosisError.value = null
  try {
    fastDiagnosis.value = await api.fastDiagnosis(incidentId.value)
  } catch (error) {
    fastDiagnosisError.value = describeError(error)
  } finally {
    fastDiagnosisBusy.value = false
  }
}

// ---------------------------------------------------------------------- 工具

function pretty(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function diagStatusText(status: string | undefined): string {
  return statusLabel(status)
}

function diagBadgeTone(status: string | undefined): string {
  return statusTone(status)
}

const canGenerateReport = computed(() => {
  const diagnosis = selectedDiagnosis.value
  if (!diagnosis) return false
  return !['pending', 'running'].includes(diagnosis.status)
})

watch(
  () => incidentId.value,
  () => {
    selectedId.value = null
    fastDiagnosis.value = null
    diagnoseError.value = null
    reportError.value = null
    refreshRecentReports()
    void detail.refresh()
    void diagnoses.refresh()
  },
)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="row">
          <RouterLink class="btn sm ghost" to="/incidents">← 返回事件列表</RouterLink>
        </div>
        <h1 class="incident-title mt-8">
          <template v-if="incident">
            {{ incident.title }}
            <span class="id">#{{ incident.id }}</span>
          </template>
          <template v-else>事件详情 #{{ incidentId || '—' }}</template>
        </h1>
        <div v-if="incident" class="page-sub">
          窗口 {{ formatTime(incident.window_start) }} ~ {{ formatTime(incident.window_end) }}
          · 创建于 {{ formatTime(incident.created_at) }}
        </div>
      </div>
      <div class="page-actions">
        <span v-if="detail.lastUpdated.value" class="faint small">
          更新于 {{ formatTime(detail.lastUpdated.value.toISOString()) }} · 3 秒轮询
          <span v-if="detail.refreshing.value" class="spinner sm"></span>
        </span>
        <button class="btn sm" :disabled="detail.refreshing.value" @click="detail.refresh()">刷新</button>
      </div>
    </div>

    <div v-if="detail.loading.value && !incident" class="panel">
      <div class="state-block">
        <span class="spinner"></span>
        <div class="state-desc">正在加载事件详情…</div>
      </div>
    </div>

    <div v-else-if="detailError && !incident" class="panel">
      <div class="state-block error">
        <div class="state-title">事件加载失败</div>
        <div class="state-desc">{{ detailError }}</div>
        <div class="row">
          <button class="btn sm" @click="detail.refresh()">重试</button>
          <RouterLink class="btn sm" to="/incidents">返回事件列表</RouterLink>
        </div>
      </div>
    </div>

    <template v-else-if="incident">
      <div v-if="detailError" class="inline-alert warn mb-16">
        <span>事件刷新失败：{{ detailError }}</span>
        <span class="spacer"></span>
        <button class="btn sm" @click="detail.refresh()">重试</button>
      </div>

      <section class="panel mb-16">
        <div class="panel-head">
          <h2>事件摘要</h2>
          <span class="panel-sub">拓扑关联与根因候选</span>
          <div class="panel-actions">
            <StatusBadge :value="incident.severity" kind="severity" />
            <StatusBadge :value="incident.status" kind="status" />
          </div>
        </div>
        <div class="panel-body">
          <div class="kv-grid mb-16">
            <div class="kv">
              <div class="kv-label">成员告警数</div>
              <div class="kv-value">{{ formatNumber(incident.alert_count) }}</div>
            </div>
            <div class="kv">
              <div class="kv-label">涉及节点</div>
              <div class="kv-value">{{ incident.nodes.length }}</div>
            </div>
            <div class="kv">
              <div class="kv-label">拓扑关联</div>
              <div class="kv-value">
                <span class="badge" :class="incident.topology_linked ? 'tone-ok' : 'tone-muted'">
                  {{ incident.topology_linked ? '已关联' : '未关联' }}
                </span>
              </div>
            </div>
            <div class="kv">
              <div class="kv-label">诊断状态</div>
              <div class="kv-value" style="font-size: 14px">
                <span class="badge" :class="`tone-${diagBadgeTone(diagnosesSorted[0]?.status)}`">
                  {{ diagnosesSorted.length ? diagStatusText(diagnosesSorted[0]?.status) : '未诊断' }}
                </span>
              </div>
            </div>
            <div class="kv">
              <div class="kv-label">批次</div>
              <div class="kv-value" style="font-size: 13px" :title="incident.batch_id ?? ''">
                {{ incident.batch_id ? incident.batch_id.slice(0, 18) : '—' }}
              </div>
            </div>
          </div>

          <div class="section-title">涉及节点</div>
          <div class="mb-16">
            <span v-for="node in incident.nodes" :key="node" class="chip">{{ node }}</span>
            <span v-if="incident.nodes.length === 0" class="faint small">无</span>
          </div>

          <div class="row">
            <button
              class="btn sm"
              :disabled="incidentActionBusy || incident.status === 'acked'"
              @click="updateIncidentStatus('ack')"
            >
              确认事件
            </button>
            <button
              class="btn sm"
              :disabled="incidentActionBusy || incident.status === 'resolved'"
              @click="updateIncidentStatus('resolve')"
            >
              标记已解决
            </button>
            <span v-if="incidentActionBusy" class="spinner sm"></span>
          </div>
          <div v-if="incidentActionError" class="inline-alert warn mt-8">
            <span>{{ incidentActionError }}</span>
          </div>
        </div>
      </section>

      <div class="grid cols-2 mb-16">
        <section class="panel">
          <div class="panel-head">
            <h2>拓扑关系图</h2>
            <span class="panel-sub">事件涉及节点高亮 · 支持缩放/拖拽</span>
          </div>
          <div class="panel-body">
            <div v-if="!topologyOption" class="chart-empty">
              <template v-if="incident.topology_linked === false">
                该事件涉及节点不在已配置拓扑中（topology_linked=false），无依赖关系可展示。
              </template>
              <template v-else>暂无拓扑数据</template>
            </div>
            <EChart v-else :option="topologyOption" height="440px" />
          </div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <h2>根因候选</h2>
            <span class="panel-sub">按 score 降序</span>
          </div>
          <div class="panel-body">
            <div v-if="rootCandidates.length === 0" class="chart-empty">暂无根因候选</div>
            <div v-else class="stack" style="gap: 12px">
              <div v-for="(candidate, index) in rootCandidates" :key="`${candidate.node}-${index}`" class="kv">
                <div class="row">
                  <strong>#{{ index + 1 }} {{ candidate.node }}</strong>
                  <StatusBadge :value="candidate.severity" kind="severity" />
                  <span class="spacer"></span>
                  <span class="mono">{{ formatPercent(candidate.score) }}</span>
                </div>
                <div class="confidence-bar mt-8">
                  <span :style="{ width: `${Math.min(100, candidate.score * 100)}%` }"></span>
                </div>
                <ul v-if="candidate.reasons?.length" class="reason-list mt-8">
                  <li v-for="reason in candidate.reasons" :key="reason">{{ reason }}</li>
                </ul>
                <div class="faint small mt-8">首次告警：{{ formatTime(candidate.first_seen) }}</div>
              </div>
            </div>

            <div class="mt-16">
              <div class="section-title">决策模型快诊（/fast-diagnosis）</div>
              <button class="btn sm" :disabled="fastDiagnosisBusy" @click="loadFastDiagnosis">
                {{ fastDiagnosisBusy ? '计算中…' : '运行规则快诊' }}
              </button>
              <div v-if="fastDiagnosisError" class="inline-alert warn mt-8">
                <span>{{ fastDiagnosisError }}</span>
              </div>
              <div v-if="fastDiagnosis" class="mt-8">
                <div class="faint small mb-16">模型版本：{{ fastDiagnosis.model_version }}</div>
                <div
                  v-for="candidate in fastDiagnosis.candidates"
                  :key="candidate.category"
                  class="kv mb-16"
                >
                  <div class="row">
                    <span class="mono">{{ candidate.category }}</span>
                    <span class="spacer"></span>
                    <span class="mono">{{ formatPercent(candidate.score) }}</span>
                  </div>
                  <div class="faint small">features: {{ candidate.features.join(', ') || '—' }}</div>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>

      <section class="panel mb-16">
        <div class="panel-head">
          <h2>诊断面板</h2>
          <span class="panel-sub">
            诊断异步执行，每 2 秒轮询进度
            <template v-if="diagnosisRunning"> · <span class="spinner sm"></span> 执行中</template>
          </span>
          <div class="panel-actions">
            <button
              class="btn primary sm"
              :disabled="diagnosing || diagnosisRunning"
              @click="startDiagnosis(false)"
            >
              {{ diagnosing ? '提交中…' : diagnosesSorted.length ? '重新诊断' : '开始诊断' }}
            </button>
            <button
              v-if="latestDiagnosis?.status === 'failed'"
              class="btn sm"
              :disabled="diagnosing || diagnosisRunning"
              @click="startDiagnosis(true)"
            >
              重试诊断
            </button>
          </div>
        </div>
        <div class="panel-body">
          <div v-if="diagnoseError" class="inline-alert warn mb-16">
            <span>{{ diagnoseError }}</span>
            <span class="spacer"></span>
            <button class="btn sm" :disabled="diagnosing" @click="startDiagnosis(true)">重试</button>
          </div>
          <div v-if="diagnosesError" class="inline-alert warn mb-16">
            <span>诊断列表刷新失败：{{ diagnosesError }}</span>
            <span class="spacer"></span>
            <button class="btn sm" @click="diagnoses.refresh()">重试</button>
          </div>

          <div v-if="diagnoses.loading.value && diagnosesSorted.length === 0" class="state-block">
            <span class="spinner"></span>
            <div class="state-desc">正在加载诊断记录…</div>
          </div>

          <div
            v-else-if="diagnosesSorted.length === 0"
            class="state-block"
          >
            <div class="state-title">尚未诊断</div>
            <div class="state-desc">点击"开始诊断"触发异步诊断；完成后将展示根因结论、推理过程与证据。</div>
            <button class="btn primary sm" :disabled="diagnosing" @click="startDiagnosis(false)">
              开始诊断
            </button>
          </div>

          <div v-else class="split-2">
            <div>
              <div class="section-title">诊断历史（共 {{ diagnosesSorted.length }} 次）</div>
              <ul class="timeline">
                <li
                  v-for="item in diagnosesSorted"
                  :key="item.id"
                  :class="{ active: item.id === selectedDiagnosis?.id }"
                  @click="selectedId = item.id"
                >
                  <div class="tl-main">
                    <div class="tl-title">
                      #{{ item.version }} · {{ item.source || '—' }}
                    </div>
                    <div class="tl-sub">{{ formatTime(item.created_at) }}</div>
                    <div v-if="item.error" class="tl-sub" style="color: var(--danger)">
                      {{ item.error }}
                    </div>
                  </div>
                  <span class="badge" :class="`tone-${diagBadgeTone(item.status)}`">
                    {{ diagStatusText(item.status) }}
                  </span>
                </li>
              </ul>
            </div>

            <div v-if="selectedDiagnosis">
              <div class="row mb-16">
                <span class="badge" :class="`tone-${diagBadgeTone(selectedDiagnosis.status)}`">
                  {{ diagStatusText(selectedDiagnosis.status) }}
                </span>
                <span v-if="selectedDiagnosis.needs_review" class="badge tone-warn">需要人工复核</span>
                <span class="faint small">
                  v{{ selectedDiagnosis.version }} · {{ selectedDiagnosis.source || '—' }}
                </span>
              </div>

              <div class="kv-grid mb-16">
                <div class="kv">
                  <div class="kv-label">根因类别</div>
                  <div class="kv-value" style="font-size: 14px">
                    {{ selectedDiagnosis.root_cause_category ?? '—' }}
                  </div>
                </div>
                <div class="kv">
                  <div class="kv-label">根因节点</div>
                  <div class="kv-value" style="font-size: 14px">
                    {{ selectedDiagnosis.root_cause_node ?? '—' }}
                  </div>
                </div>
                <div class="kv">
                  <div class="kv-label">置信度</div>
                  <div class="kv-value">
                    {{ formatPercent(selectedDiagnosis.confidence) }}
                  </div>
                </div>
                <div class="kv">
                  <div class="kv-label">耗时</div>
                  <div class="kv-value">{{ formatDuration(selectedDiagnosis.latency_ms) }}</div>
                </div>
              </div>

              <div
                v-if="selectedDiagnosis.confidence !== null"
                class="confidence-bar mb-16"
                :title="`置信度 ${formatPercent(selectedDiagnosis.confidence)}`"
              >
                <span
                  :style="{
                    width: `${Math.min(100, Math.max(0, (selectedDiagnosis.confidence ?? 0) * 100))}%`,
                  }"
                ></span>
              </div>

              <div class="faint small mb-16">
                模型：{{ selectedDiagnosis.model ?? '—' }} · Prompt：{{
                  selectedDiagnosis.prompt_version ?? '—'
                }}
                <template v-if="selectedDiagnosis.token_usage">
                  · Token：{{ selectedDiagnosis.token_usage.prompt_tokens ?? 0 }} +
                  {{ selectedDiagnosis.token_usage.completion_tokens ?? 0 }}
                </template>
              </div>

              <div v-if="selectedDiagnosis.error" class="inline-alert mb-16">
                <span>诊断失败原因：{{ selectedDiagnosis.error }}</span>
              </div>

              <div class="section-title">推理过程</div>
              <p class="muted mb-16" style="white-space: pre-wrap">
                {{ selectedDiagnosis.reasoning || '（无推理内容）' }}
              </p>

              <div class="section-title">缺失证据</div>
              <div class="mb-16">
                <template v-if="selectedDiagnosis.missing_evidence?.length">
                  <span
                    v-for="item in selectedDiagnosis.missing_evidence"
                    :key="item"
                    class="chip"
                    style="color: var(--warn)"
                  >
                    {{ item }}
                  </span>
                </template>
                <span v-else class="faint small">无</span>
              </div>

              <div class="section-title">证据</div>
              <pre class="raw-pre">{{ pretty(selectedDiagnosis.evidence ?? {}) }}</pre>
            </div>
          </div>
        </div>
      </section>

      <section class="panel mb-16">
        <div class="panel-head">
          <h2>诊断报告</h2>
          <span class="panel-sub">基于当前选中的诊断版本生成</span>
          <div class="panel-actions">
            <button
              class="btn primary sm"
              :disabled="!canGenerateReport || reportBusy"
              @click="generateReport"
            >
              {{ reportBusy ? '生成中…' : '生成报告' }}
            </button>
          </div>
        </div>
        <div class="panel-body">
          <div v-if="reportError" class="inline-alert warn mb-16">
            <span>{{ reportError }}</span>
            <span class="spacer"></span>
            <button class="btn sm" @click="generateReport">重试</button>
          </div>
          <div class="muted small mb-16">
            <template v-if="canGenerateReport">
              将使用诊断 v{{ selectedDiagnosis?.version }} 生成 Markdown/JSON 报告，生成后自动跳转报告页。
            </template>
            <template v-else>需先完成一次诊断（状态非排队中/执行中）才能生成报告。</template>
          </div>
          <div v-if="reports.length" class="row">
            <span class="faint small">最近报告：</span>
            <RouterLink
              v-for="report in reports"
              :key="report.id"
              class="btn sm"
              :to="`/reports/${report.id}`"
            >
              报告 #{{ report.id }}（{{ report.riskLevel || '—' }}）
            </RouterLink>
          </div>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>成员告警</h2>
          <span class="panel-sub">共 {{ incident.alerts?.length ?? 0 }} 条（事件 alert_count {{ incident.alert_count }}）</span>
        </div>
        <div v-if="!incident.alerts || incident.alerts.length === 0" class="state-block">
          <div class="state-title">无成员告警</div>
          <div class="state-desc">事件详情响应中未包含成员告警。</div>
        </div>
        <div v-else class="table-wrap">
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
              </tr>
            </thead>
            <tbody>
              <tr v-for="alert in incident.alerts" :key="alert.id">
                <td class="mono">{{ alert.id }}</td>
                <td><StatusBadge :value="alert.severity" kind="severity" /></td>
                <td><StatusBadge :value="alert.status" kind="status" /></td>
                <td class="mono">{{ alert.host }}</td>
                <td class="mono">{{ alert.service }}</td>
                <td style="max-width: 320px">
                  <div>{{ alert.rule }}</div>
                  <div class="faint small">{{ alert.message }}</div>
                </td>
                <td class="nowrap">{{ formatTime(alert.first_seen) }}</td>
                <td class="nowrap">{{ formatTime(alert.last_seen) }}</td>
                <td class="num">{{ alert.repeat_count }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>

    <div v-else class="panel">
      <div class="state-block error">
        <div class="state-title">事件不可用</div>
        <div class="state-desc">{{ detailError ?? '未找到该事件' }}</div>
        <RouterLink class="btn sm" to="/incidents">返回事件列表</RouterLink>
      </div>
    </div>
  </div>
</template>

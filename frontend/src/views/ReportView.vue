<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { describeError } from '@/api/client'
import { api } from '@/api/endpoints'
import StatusBadge from '@/components/StatusBadge.vue'
import { useLiveQuery } from '@/composables/useLiveQuery'
import { formatTime, severityTone } from '@/utils/format'
import type { RecentReport } from '@/utils/recentReports'
import { loadRecentReports, rememberReport, rememberReportId } from '@/utils/recentReports'

const route = useRoute()
const router = useRouter()

const reportId = computed(() => {
  const raw = route.params.id
  const parsed = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 0
})

const hasReportId = computed(() => reportId.value > 0)

const { data: report, error, loading, refresh } = useLiveQuery(
  (signal) => {
    if (!hasReportId.value) return Promise.resolve(null)
    return api.getReport(reportId.value, signal)
  },
  { interval: 0, immediate: true },
)

const errorText = computed(() => (error.value ? describeError(error.value) : null))
const content = computed(() => report.value?.content ?? null)
const steps = computed(() => content.value?.steps ?? [])

function riskTone(risk: string | null | undefined): string {
  if (!risk) return 'muted'
  const normalized = risk.toLowerCase()
  if (normalized === 'high' || normalized === 'critical') return 'danger'
  if (normalized === 'medium' || normalized === 'mid') return 'warn'
  if (normalized === 'low') return 'ok'
  return severityTone(normalized)
}

// 记录最近报告，供导航"报告"页展示与快速回访
watch(
  () => report.value,
  (value) => {
    if (value) rememberReport(value)
  },
)

const recent = ref<RecentReport[]>([])
const manualId = ref('')

function refreshRecent(): void {
  recent.value = loadRecentReports()
}

onMounted(refreshRecent)

function openManual(): void {
  const id = Number(manualId.value)
  if (Number.isInteger(id) && id > 0) {
    rememberReportId(id)
    void router.push(`/reports/${id}`)
    manualId.value = ''
  }
}

watch(reportId, () => {
  refreshRecent()
  void refresh()
})
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <div class="row">
          <RouterLink class="btn sm ghost" to="/reports">← 报告列表</RouterLink>
        </div>
        <h1 class="mt-8">
          <template v-if="report">诊断报告 #{{ report.id }}</template>
          <template v-else-if="hasReportId">诊断报告 #{{ reportId }}</template>
          <template v-else>报告中心</template>
        </h1>
        <div v-if="report" class="page-sub">
          事件
          <RouterLink :to="`/incidents/${report.incident_id}`">#{{ report.incident_id }}</RouterLink>
          · 报告 v{{ report.version }} · 生成于 {{ formatTime(report.created_at) }}
        </div>
      </div>
      <div v-if="report" class="page-actions">
        <a
          class="btn sm"
          :href="api.reportExportUrl(report.id, 'markdown')"
          target="_blank"
          rel="noopener"
        >
          导出 Markdown
        </a>
        <a
          class="btn sm"
          :href="api.reportExportUrl(report.id, 'json')"
          target="_blank"
          rel="noopener"
        >
          导出 JSON
        </a>
        <button class="btn sm" :disabled="loading" @click="refresh()">刷新</button>
      </div>
    </div>

    <!-- 无报告 ID：报告中心（最近报告 + 手动打开） -->
    <template v-if="!hasReportId">
      <section class="panel mb-16">
        <div class="panel-head">
          <h2>打开报告</h2>
          <span class="panel-sub">报告由事件详情页的诊断面板生成</span>
        </div>
        <div class="panel-body">
          <form class="filters" @submit.prevent="openManual">
            <div class="field">
              <label for="report-id">报告 ID</label>
              <input
                id="report-id"
                v-model="manualId"
                class="input"
                inputmode="numeric"
                placeholder="如 1"
              />
            </div>
            <button class="btn primary" type="submit" :disabled="!manualId.trim()">打开报告</button>
            <RouterLink class="btn" to="/incidents">从事件列表进入</RouterLink>
          </form>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>最近报告</h2>
          <span class="panel-sub">保存在本浏览器（最多 10 条）</span>
        </div>
        <div v-if="recent.length === 0" class="state-block">
          <div class="state-title">暂无最近报告</div>
          <div class="state-desc">
            在事件详情页完成诊断后点击"生成报告"，或输入报告 ID 打开已有报告。
          </div>
          <RouterLink class="btn sm" to="/incidents">前往故障事件</RouterLink>
        </div>
        <div v-else class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>报告 ID</th>
                <th>事件</th>
                <th>标题</th>
                <th>风险等级</th>
                <th>创建时间</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in recent" :key="item.id">
                <td class="mono">#{{ item.id }}</td>
                <td>
                  <RouterLink v-if="item.incidentId" :to="`/incidents/${item.incidentId}`">
                    #{{ item.incidentId }}
                  </RouterLink>
                  <span v-else>—</span>
                </td>
                <td>{{ item.title }}</td>
                <td>
                  <span class="badge" :class="`tone-${riskTone(item.riskLevel)}`">
                    {{ item.riskLevel || '—' }}
                  </span>
                </td>
                <td class="nowrap">{{ formatTime(item.createdAt) }}</td>
                <td><RouterLink class="btn sm" :to="`/reports/${item.id}`">查看</RouterLink></td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </template>

    <!-- 有报告 ID -->
    <template v-else>
      <div v-if="loading && !report" class="panel">
        <div class="state-block">
          <span class="spinner"></span>
          <div class="state-desc">正在加载报告 #{{ reportId }}…</div>
        </div>
      </div>

      <div v-else-if="errorText && !report" class="panel">
        <div class="state-block error">
          <div class="state-title">报告加载失败</div>
          <div class="state-desc">{{ errorText }}</div>
          <div class="row">
            <button class="btn sm" @click="refresh()">重试</button>
            <RouterLink class="btn sm" to="/incidents">返回事件列表</RouterLink>
          </div>
        </div>
      </div>

      <template v-else-if="report">
        <div v-if="errorText" class="inline-alert warn mb-16">
          <span>刷新失败：{{ errorText }}</span>
          <span class="spacer"></span>
          <button class="btn sm" @click="refresh()">重试</button>
        </div>

        <section class="panel mb-16">
          <div class="panel-head">
            <h2>报告概要</h2>
            <div class="panel-actions">
              <span class="badge" :class="`tone-${riskTone(report.risk_level)}`">
                风险等级：{{ report.risk_level || '—' }}
              </span>
              <span class="badge tone-info">v{{ report.version }}</span>
            </div>
          </div>
          <div class="panel-body">
            <div class="report-section">
              <h3>摘要</h3>
              <p>{{ content?.summary || '（无摘要）' }}</p>
            </div>
            <div class="report-section">
              <h3>影响范围</h3>
              <p>{{ content?.impact || '（无影响范围描述）' }}</p>
            </div>
            <div class="report-section">
              <h3>根因</h3>
              <p>{{ content?.root_cause || '（无根因描述）' }}</p>
            </div>

            <div class="report-section">
              <h3>证据</h3>
              <ul v-if="content?.evidence?.length" class="evidence-list">
                <li v-for="(item, index) in content.evidence" :key="index">{{ item }}</li>
              </ul>
              <p v-else class="muted">（无证据条目）</p>
            </div>
          </div>
        </section>

        <section class="panel mb-16">
          <div class="panel-head">
            <h2>分步排查与修复</h2>
            <span class="panel-sub">共 {{ steps.length }} 步 · 高风险操作需人工确认</span>
          </div>
          <div v-if="steps.length === 0" class="state-block">
            <div class="state-title">无处置步骤</div>
            <div class="state-desc">报告未包含 steps 字段。</div>
          </div>
          <div v-else class="table-wrap">
            <table class="table">
              <thead>
                <tr>
                  <th style="width: 54px">序号</th>
                  <th>操作标题</th>
                  <th>动作</th>
                  <th>预期结果</th>
                  <th>风险</th>
                  <th>回滚动作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="step in steps" :key="step.order">
                  <td class="mono">{{ step.order }}</td>
                  <td>{{ step.title }}</td>
                  <td style="max-width: 260px">{{ step.action }}</td>
                  <td style="max-width: 220px">{{ step.expected }}</td>
                  <td>
                    <span class="badge" :class="`tone-${riskTone(step.risk)}`">
                      {{ step.risk || '—' }}
                    </span>
                  </td>
                  <td style="max-width: 240px">{{ step.rollback }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <div class="grid cols-2 mb-16">
          <section class="panel">
            <div class="panel-head"><h2>验证方式</h2></div>
            <div class="panel-body">
              <p class="muted" style="white-space: pre-wrap">{{ content?.verification || '（无）' }}</p>
            </div>
          </section>
          <section class="panel">
            <div class="panel-head"><h2>回滚方案</h2></div>
            <div class="panel-body">
              <p class="muted" style="white-space: pre-wrap">{{ content?.rollback || '（无）' }}</p>
            </div>
          </section>
        </div>

        <section class="panel mb-16">
          <div class="panel-head"><h2>风险提示</h2></div>
          <div class="panel-body">
            <div class="inline-alert warn">
              <span>{{ content?.risk_notice || '（无风险提示）' }}</span>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-head">
            <h2>原始 Markdown</h2>
            <span class="panel-sub">与导出文件内容一致</span>
          </div>
          <div class="panel-body">
            <details class="raw-block" open>
              <summary>展开 / 收起 Markdown 源文</summary>
              <pre class="raw-pre mt-8">{{ report.markdown || '（无 markdown 内容）' }}</pre>
            </details>
          </div>
        </section>
      </template>

      <div v-else class="panel">
        <div class="state-block">
          <div class="state-title">报告不存在</div>
          <div class="state-desc">未找到报告 #{{ reportId }}，可能已被删除或 ID 不正确。</div>
          <RouterLink class="btn sm" to="/incidents">返回事件列表</RouterLink>
        </div>
      </div>
    </template>
  </div>
</template>

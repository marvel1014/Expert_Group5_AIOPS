<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'

import { describeError } from '@/api/client'
import type { SimulateRequest, SimulateResponse } from '@/api/types'
import { api } from '@/api/endpoints'
import { useLiveQuery } from '@/composables/useLiveQuery'
import { formatTime } from '@/utils/format'

const scenarios = useLiveQuery((signal) => api.listScenarios(signal), { interval: 30_000 })

const mode = ref<'scenario' | 'burst'>('scenario')
const scenarioId = ref('')
const compression = ref('10')
const burstHost = ref('order-host-01')
const burstService = ref('order-service')
const burstCount = ref('30')
const burstRule = ref('CPU usage 98%')

const submitting = ref(false)
const submitError = ref<string | null>(null)
const results = ref<{ response: SimulateResponse; at: string; summary: string }[]>([])

const scenarioOptions = computed(() => scenarios.data.value?.items ?? [])
const selectedScenario = computed(
  () => scenarioOptions.value.find((item) => item.id === scenarioId.value) ?? null,
)
const scenariosError = computed(() =>
  scenarios.error.value ? describeError(scenarios.error.value) : null,
)

async function submit(): Promise<void> {
  submitting.value = true
  submitError.value = null
  try {
    let body: SimulateRequest
    let summary: string
    if (mode.value === 'scenario') {
      if (!scenarioId.value) {
        submitError.value = '请先选择一个故障场景。'
        return
      }
      const value = Number(compression.value)
      body = {
        scenario_id: scenarioId.value,
        compression: Number.isFinite(value) && value > 0 ? value : undefined,
      }
      summary = `场景 ${scenarioId.value} 回放`
    } else {
      const count = Number(burstCount.value)
      body = {
        mode: 'burst',
        host: burstHost.value.trim(),
        service: burstService.value.trim(),
        count: Number.isFinite(count) && count > 0 ? count : 1,
        rule: burstRule.value.trim() || undefined,
      }
      summary = `Burst 注入 ${body.host} / ${body.service}（${body.count} 条）`
    }

    const response = await api.simulate(body)
    results.value.unshift({ response, at: new Date().toISOString(), summary })
    results.value = results.value.slice(0, 8)
  } catch (error) {
    submitError.value = describeError(error)
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1>模拟注入</h1>
        <div class="page-sub">
          向平台注入一次故障场景或 burst 告警，注入完成后后端自动触发降噪、拓扑关联与事件生成
        </div>
      </div>
      <div class="page-actions">
        <RouterLink class="btn sm" to="/incidents">查看事件</RouterLink>
        <RouterLink class="btn sm" to="/">查看概览</RouterLink>
      </div>
    </div>

    <div class="grid cols-2">
      <section class="panel">
        <div class="panel-head">
          <h2>注入配置</h2>
          <div class="panel-actions">
            <button
              class="btn sm"
              :class="{ primary: mode === 'scenario' }"
              @click="mode = 'scenario'"
            >
              场景回放
            </button>
            <button class="btn sm" :class="{ primary: mode === 'burst' }" @click="mode = 'burst'">
              Burst 注入
            </button>
          </div>
        </div>
        <div class="panel-body">
          <form class="stack" style="gap: 14px" @submit.prevent="submit">
            <template v-if="mode === 'scenario'">
              <div v-if="scenariosError" class="inline-alert warn">
                <span>场景库加载失败：{{ scenariosError }}</span>
                <span class="spacer"></span>
                <button class="btn sm" type="button" @click="scenarios.refresh()">重试</button>
              </div>
              <div v-if="scenarios.loading.value && !scenarios.data.value" class="state-block">
                <span class="spinner"></span>
                <div class="state-desc">正在加载场景库…</div>
              </div>
              <div v-else-if="scenarioOptions.length === 0" class="state-block">
                <div class="state-title">场景库为空</div>
                <div class="state-desc">未加载到内置故障场景，请运行 make seed 或检查 scenarios 目录。</div>
              </div>
              <template v-else>
                <div class="field">
                  <label for="sim-scenario">故障场景</label>
                  <select id="sim-scenario" v-model="scenarioId" class="select">
                    <option value="">请选择场景</option>
                    <option v-for="item in scenarioOptions" :key="item.id" :value="item.id">
                      {{ item.name }}（{{ item.id }}）
                    </option>
                  </select>
                </div>
                <div v-if="selectedScenario" class="kv">
                  <div class="faint small">{{ selectedScenario.description }}</div>
                  <div class="faint small mt-8">
                    类别 {{ selectedScenario.category }} · 告警 {{ selectedScenario.alert_count }} 条 ·
                    日志 {{ selectedScenario.log_count }} 条
                  </div>
                  <div class="faint small mt-8">
                    期望根因：{{ selectedScenario.expected.root_cause_category ?? '—' }} /
                    {{ selectedScenario.expected.root_cause_node ?? '—' }}
                  </div>
                </div>
                <div class="field">
                  <label for="sim-compression">时间压缩倍率</label>
                  <input
                    id="sim-compression"
                    v-model="compression"
                    class="input"
                    inputmode="decimal"
                    placeholder="如 10"
                  />
                  <span class="faint small">场景内相对时间按该倍率压缩为真实时间戳</span>
                </div>
              </template>
            </template>

            <template v-else>
              <div class="field">
                <label for="burst-host">主机</label>
                <input id="burst-host" v-model="burstHost" class="input" placeholder="order-host-01" />
              </div>
              <div class="field">
                <label for="burst-service">服务</label>
                <input
                  id="burst-service"
                  v-model="burstService"
                  class="input"
                  placeholder="order-service"
                />
              </div>
              <div class="field">
                <label for="burst-count">告警条数</label>
                <input
                  id="burst-count"
                  v-model="burstCount"
                  class="input"
                  inputmode="numeric"
                  placeholder="30"
                />
              </div>
              <div class="field">
                <label for="burst-rule">告警规则</label>
                <input
                  id="burst-rule"
                  v-model="burstRule"
                  class="input"
                  placeholder="CPU usage 98%"
                />
              </div>
            </template>

            <div class="row">
              <button class="btn primary" type="submit" :disabled="submitting">
                {{ submitting ? '注入中…' : '开始注入' }}
              </button>
              <span v-if="submitting" class="spinner sm"></span>
            </div>
          </form>

          <div v-if="submitError" class="inline-alert warn mt-16">
            <span>{{ submitError }}</span>
          </div>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>注入结果</h2>
          <span class="panel-sub">最近 8 次</span>
        </div>
        <div v-if="results.length === 0" class="state-block">
          <div class="state-title">尚无注入记录</div>
          <div class="state-desc">提交一次注入后，这里会显示批次、告警/日志数量与生成的事件。</div>
        </div>
        <div v-else class="panel-body stack" style="gap: 12px">
          <div v-for="(item, index) in results" :key="`${item.response.batch_id}-${index}`" class="kv">
            <div class="row">
              <strong>{{ item.summary }}</strong>
              <span class="spacer"></span>
              <span class="faint small">{{ formatTime(item.at) }}</span>
            </div>
            <div class="mt-8 mono small" style="word-break: break-all">
              批次：{{ item.response.batch_id }}
            </div>
            <div class="row mt-8">
              <span class="chip">告警 {{ item.response.alert_count }}</span>
              <span class="chip">日志 {{ item.response.log_count }}</span>
              <span class="chip">事件 {{ item.response.incident_ids.length }}</span>
            </div>
            <div v-if="item.response.incident_ids.length" class="row mt-8">
              <span class="faint small">生成事件：</span>
              <RouterLink
                v-for="incidentId in item.response.incident_ids"
                :key="incidentId"
                class="btn sm"
                :to="`/incidents/${incidentId}`"
              >
                #{{ incidentId }}
              </RouterLink>
            </div>
            <div v-else class="faint small mt-8">本次注入未生成事件（可能未命中关联窗口或拓扑）。</div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

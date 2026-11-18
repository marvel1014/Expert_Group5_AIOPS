<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

import { connectionMessage, connectionOnline, triggerRetry } from '@/api/connection'
import { api } from '@/api/endpoints'
import { useLiveQuery } from '@/composables/useLiveQuery'
import { formatShortTime } from '@/utils/format'

const route = useRoute()

const navItems = [
  { to: '/', label: '概览' },
  { to: '/alerts', label: '告警' },
  { to: '/incidents', label: '事件' },
  { to: '/reports', label: '报告' },
  { to: '/evaluations', label: '评估' },
  { to: '/simulate', label: '模拟注入' },
]

const appVersion = __APP_VERSION__

const { data: health, error: healthError, lastUpdated, refresh } = useLiveQuery(
  (signal) => api.health(signal),
  { interval: 15_000 },
)

const llmModeLabel = computed(() => {
  const mode = health.value?.llm_mode
  if (!mode) return '—'
  if (mode === 'mock') return 'Mock 模式'
  if (mode === 'llm') return '真实 LLM'
  return `自动（${mode}）`
})

function isActive(path: string): boolean {
  if (path === '/') return route.path === '/'
  if (path === '/incidents') return route.path.startsWith('/incidents')
  if (path === '/reports') return route.path.startsWith('/reports')
  return route.path === path
}

function retryConnection(): void {
  triggerRetry()
  void refresh()
}
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <RouterLink to="/" class="brand">
        <span class="brand-dot"></span>
        AIOps 运维看板
        <small>告警 · 事件 · 诊断</small>
      </RouterLink>
      <nav class="main-nav">
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          :class="{ 'router-link-active': isActive(item.to) }"
        >
          {{ item.label }}
        </RouterLink>
      </nav>
      <span class="spacer"></span>
      <span class="header-status">
        <span class="dot" :class="{ offline: !connectionOnline }"></span>
        <template v-if="connectionOnline">
          {{ healthError ? '后端健康检查失败' : '已连接' }}
          <template v-if="lastUpdated"> · {{ formatShortTime(lastUpdated.toISOString()) }}</template>
        </template>
        <template v-else>连接异常</template>
      </span>
    </header>

    <div v-if="!connectionOnline" class="connection-banner">
      <strong>连接异常</strong>
      <span>{{ connectionMessage || '无法连接后端服务，页面会自动重试。' }}</span>
      <span class="spacer"></span>
      <button class="btn sm" @click="retryConnection">立即重试</button>
    </div>

    <main class="app-main">
      <RouterView />
    </main>

    <footer class="app-footer">
      <span>前端 v{{ appVersion }}</span>
      <span v-if="health">
        后端 v{{ health.version }} · {{ llmModeLabel }} · 数据库 {{ health.database }}
      </span>
      <span v-else>后端状态未知（接口 /healthz 不可用）</span>
      <span>API 基址 /api/v1 · 自动刷新 3 秒</span>
    </footer>
  </div>
</template>

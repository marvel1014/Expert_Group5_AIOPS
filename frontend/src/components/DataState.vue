<script setup lang="ts">
withDefaults(
  defineProps<{
    loading: boolean
    error?: string | null
    empty?: boolean
    emptyText?: string
    loadingText?: string
    /** 已有旧数据但刷新失败时，显示内联错误而非整块错误 */
    hasData?: boolean
  }>(),
  {
    error: null,
    empty: false,
    emptyText: '暂无数据',
    loadingText: '加载中…',
    hasData: false,
  },
)

const emit = defineEmits<{
  (event: 'retry'): void
}>()
</script>

<template>
  <div v-if="loading && !hasData" class="state-block">
    <span class="spinner"></span>
    <div class="state-desc">{{ loadingText }}</div>
  </div>
  <div v-else-if="error && !hasData" class="state-block error">
    <div class="state-title">加载失败</div>
    <div class="state-desc">{{ error }}</div>
    <button class="btn sm" @click="emit('retry')">重试</button>
  </div>
  <div v-else-if="empty" class="state-block">
    <div class="state-title">空状态</div>
    <div class="state-desc">{{ emptyText }}</div>
    <slot name="empty-action"></slot>
  </div>
  <template v-else>
    <div v-if="error" class="inline-alert warn" style="margin: 12px 18px">
      <span>刷新失败：{{ error }}</span>
      <span class="spacer"></span>
      <button class="btn sm" @click="emit('retry')">重试</button>
    </div>
    <slot></slot>
  </template>
</template>

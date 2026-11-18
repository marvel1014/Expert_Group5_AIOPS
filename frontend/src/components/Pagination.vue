<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    page: number
    pageSize: number
    total: number
    pageSizeOptions?: number[]
  }>(),
  { pageSizeOptions: () => [10, 20, 50, 100] },
)

const emit = defineEmits<{
  (event: 'update:page', value: number): void
  (event: 'update:pageSize', value: number): void
}>()

const totalPages = computed(() => Math.max(1, Math.ceil(props.total / Math.max(1, props.pageSize))))
const canPrev = computed(() => props.page > 1)
const canNext = computed(() => props.page < totalPages.value)

function go(page: number): void {
  if (page >= 1 && page <= totalPages.value && page !== props.page) {
    emit('update:page', page)
  }
}

function changePageSize(event: Event): void {
  const value = Number((event.target as HTMLSelectElement).value)
  if (Number.isFinite(value) && value > 0) emit('update:pageSize', value)
}
</script>

<template>
  <div class="pagination">
    <span class="page-info">
      共 <strong>{{ total }}</strong> 条 · 第 {{ page }} / {{ totalPages }} 页
    </span>
    <span class="spacer"></span>
    <label class="faint small">每页</label>
    <select class="select" style="width: 76px" :value="pageSize" @change="changePageSize">
      <option v-for="size in pageSizeOptions" :key="size" :value="size">{{ size }}</option>
    </select>
    <button class="btn sm" :disabled="!canPrev" @click="go(page - 1)">上一页</button>
    <button class="btn sm" :disabled="!canNext" @click="go(page + 1)">下一页</button>
  </div>
</template>

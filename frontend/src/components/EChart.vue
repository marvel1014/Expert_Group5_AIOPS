<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, GraphChart, PieChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

import type { EChartsOption } from 'echarts'

echarts.use([
  BarChart,
  GraphChart,
  PieChart,
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
  CanvasRenderer,
])

const props = withDefaults(
  defineProps<{
    option: EChartsOption
    height?: string
  }>(),
  { height: '320px' },
)

const container = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let observer: ResizeObserver | null = null

function render(): void {
  if (!container.value) return
  if (!chart) chart = echarts.init(container.value)
  chart.setOption(props.option, true)
}

function onResize(): void {
  chart?.resize()
}

watch(
  () => props.option,
  () => render(),
  { deep: true },
)

onMounted(() => {
  render()
  if (container.value && typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(onResize)
    observer.observe(container.value)
  }
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
  window.removeEventListener('resize', onResize)
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div ref="container" class="chart-box" :style="{ height }"></div>
</template>

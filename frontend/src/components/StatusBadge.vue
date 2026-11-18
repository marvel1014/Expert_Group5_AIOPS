<script setup lang="ts">
import { computed } from 'vue'

import { severityLabel, severityTone, statusLabel, statusTone } from '@/utils/format'

const props = withDefaults(
  defineProps<{
    value: string | null | undefined
    kind?: 'severity' | 'status'
    label?: string
  }>(),
  { kind: 'status', label: undefined },
)

const text = computed(() => {
  if (props.label) return props.label
  return props.kind === 'severity' ? severityLabel(props.value) : statusLabel(props.value)
})

const tone = computed(() =>
  props.kind === 'severity' ? severityTone(props.value) : statusTone(props.value),
)
</script>

<template>
  <span class="badge" :class="`tone-${tone}`">{{ text }}</span>
</template>

import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import type { Ref } from 'vue'

import { retryTick } from '@/api/connection'

export interface LiveQueryOptions {
  /** 轮询间隔（毫秒），0 表示只请求一次不轮询。默认 3000。 */
  interval?: number
  /** 是否立即执行首次请求，默认 true。 */
  immediate?: boolean
  /** 页面不可见时暂停，恢复可见后自动继续，默认 true。 */
  pauseWhenHidden?: boolean
}

export interface LiveQueryResult<T> {
  data: Ref<T | null>
  error: Ref<unknown>
  /** 首次加载中（尚未拿到任何数据）。 */
  loading: Ref<boolean>
  /** 已有数据时的后台刷新中。 */
  refreshing: Ref<boolean>
  lastUpdated: Ref<Date | null>
  refresh: () => Promise<void>
}

export const DEFAULT_POLL_INTERVAL = 3000

/**
 * 轮询查询封装：3 秒刷新、页面隐藏时暂停、连接恢复（retryTick）后自动重试。
 * 后续若后端提供 SSE，只需在此处替换实现，页面无需改动。
 */
export function useLiveQuery<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  options: LiveQueryOptions = {},
): LiveQueryResult<T> {
  const { interval = DEFAULT_POLL_INTERVAL, immediate = true, pauseWhenHidden = true } = options

  const data = shallowRef<T | null>(null)
  const error = ref<unknown>(null)
  const loading = ref<boolean>(immediate)
  const refreshing = ref(false)
  const lastUpdated = ref<Date | null>(null)

  let timer: ReturnType<typeof setInterval> | null = null
  let controller: AbortController | null = null
  let inFlight = false
  let stopped = false

  async function run(): Promise<void> {
    if (stopped || inFlight) return
    if (pauseWhenHidden && typeof document !== 'undefined' && document.hidden) return

    inFlight = true
    controller = new AbortController()
    const current = controller
    if (data.value === null) loading.value = true
    else refreshing.value = true

    try {
      const result = await fetcher(current.signal)
      if (stopped) return
      data.value = result
      error.value = null
      lastUpdated.value = new Date()
    } catch (err) {
      if (stopped || current.signal.aborted) return
      error.value = err
    } finally {
      if (!current.signal.aborted) {
        loading.value = false
        refreshing.value = false
      }
      inFlight = false
    }
  }

  function startTimer(): void {
    stopTimer()
    if (interval > 0) {
      timer = setInterval(() => {
        void run()
      }, interval)
    }
  }

  function stopTimer(): void {
    if (timer !== null) {
      clearInterval(timer)
      timer = null
    }
  }

  function onVisibilityChange(): void {
    if (!pauseWhenHidden) return
    if (!document.hidden) void run()
  }

  const stopRetryWatch = watch(retryTick, () => {
    void run()
  })

  onMounted(() => {
    if (pauseWhenHidden) document.addEventListener('visibilitychange', onVisibilityChange)
    startTimer()
    if (immediate) void run()
  })

  onBeforeUnmount(() => {
    stopped = true
    stopTimer()
    stopRetryWatch()
    controller?.abort()
    if (pauseWhenHidden) document.removeEventListener('visibilitychange', onVisibilityChange)
  })

  return { data, error, loading, refreshing, lastUpdated, refresh: run }
}

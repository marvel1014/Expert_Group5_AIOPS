import { ref } from 'vue'

/**
 * 全局连接状态：任何一次 API 请求成功即视为连接正常；
 * 网络失败 / 超时 / 502-504 视为后端不可用，由 App.vue 展示"连接异常"提示。
 */
export const connectionOnline = ref(true)
export const connectionMessage = ref('')
/** 递增该值会让所有 useLiveQuery 立即重试，用于"重试"按钮。 */
export const retryTick = ref(0)

export function markConnectionOk(): void {
  connectionOnline.value = true
  connectionMessage.value = ''
}

export function markConnectionFailure(message: string): void {
  connectionOnline.value = false
  connectionMessage.value = message
}

/** 用户点击"重试"：所有在用的轮询查询立刻重新拉取。 */
export function triggerRetry(): void {
  retryTick.value += 1
}

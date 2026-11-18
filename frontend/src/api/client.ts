import { markConnectionFailure, markConnectionOk } from './connection'
import type { ErrorBody } from './types'

/** API 前缀，后端所有业务接口都在 /api/v1 下（/healthz 除外）。 */
export const API_BASE = '/api/v1'

const DEFAULT_TIMEOUT_MS = 15_000

export type QueryValue = string | number | boolean | null | undefined
export type QueryParams = Record<string, QueryValue>

export interface RequestConfig {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  query?: QueryParams
  body?: unknown
  timeoutMs?: number
  signal?: AbortSignal
  headers?: Record<string, string>
}

/** 统一 API 错误，区分网络不可用、HTTP 状态与后端业务错误。 */
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: unknown

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }

  /** 网络不可用 / 超时（请求没有拿到任何 HTTP 响应）。 */
  get isNetwork(): boolean {
    return this.status === 0
  }

  get isTimeout(): boolean {
    return this.status === 0 && this.code === 'timeout'
  }

  get isNotFound(): boolean {
    return this.status === 404
  }

  get isUnauthorized(): boolean {
    return this.status === 401 || this.status === 403
  }

  get isServerError(): boolean {
    return this.status >= 500
  }
}

/** 把查询参数拼到 URL 上，跳过空值。 */
export function buildUrl(path: string, query?: QueryParams): string {
  if (!query) return path
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue
    search.append(key, String(value))
  }
  const qs = search.toString()
  return qs ? `${path}?${qs}` : path
}

function networkError(error: unknown, timedOut: boolean): ApiError {
  if (timedOut) {
    return new ApiError(0, 'timeout', '请求超时：后端服务在限定时间内没有响应')
  }
  const detail = error instanceof Error ? error.message : String(error)
  return new ApiError(0, 'network', `无法连接后端服务：${detail}`)
}

async function parseErrorBody(response: Response): Promise<ApiError> {
  let code = 'http_error'
  let message = `请求失败（HTTP ${response.status}）`
  let details: unknown

  try {
    const data = (await response.json()) as ErrorBody
    if (data && data.error) {
      if (typeof data.error.code === 'string' && data.error.code) code = data.error.code
      if (typeof data.error.message === 'string' && data.error.message) {
        message = data.error.message
      }
      details = data.error.details
    } else if (data && data.detail !== undefined) {
      message =
        typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
    }
  } catch {
    // 响应体不是 JSON（或为空），保留默认描述
  }

  if (response.status === 401) message = `未授权（401）：${message}`
  else if (response.status === 403) message = `无访问权限（403）：${message}`
  else if (response.status === 404) message = `资源不存在（404）：${message}`
  else if (response.status >= 500) message = `后端服务异常（${response.status}）：${message}`

  return new ApiError(response.status, code, message, details)
}

function looksOffline(status: number): boolean {
  return status === 0 || status === 502 || status === 503 || status === 504
}

/**
 * 统一 fetch 封装：JSON 序列化、查询串、超时、错误体解析与连接状态上报。
 */
export async function request<T>(path: string, config: RequestConfig = {}): Promise<T> {
  const {
    method = 'GET',
    query,
    body,
    timeoutMs = DEFAULT_TIMEOUT_MS,
    signal,
    headers = {},
  } = config

  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)

  const onExternalAbort = () => controller.abort()
  if (signal) {
    if (signal.aborted) controller.abort()
    else signal.addEventListener('abort', onExternalAbort)
  }

  let response: Response
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      headers: {
        Accept: 'application/json',
        ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...headers,
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
      credentials: 'same-origin',
    })
  } catch (error) {
    // 外部主动取消（组件卸载 / 轮询换代）不应当作连接异常上报
    const abortedByCaller = signal?.aborted === true && !timedOut
    if (abortedByCaller) {
      throw new ApiError(0, 'aborted', '请求已取消')
    }
    const apiError = networkError(error, timedOut)
    markConnectionFailure(apiError.message)
    throw apiError
  } finally {
    clearTimeout(timer)
    signal?.removeEventListener('abort', onExternalAbort)
  }

  if (!response.ok) {
    const apiError = await parseErrorBody(response)
    if (looksOffline(response.status)) markConnectionFailure(apiError.message)
    else markConnectionOk()
    throw apiError
  }

  markConnectionOk()
  if (response.status === 204) return undefined as T
  const contentType = response.headers.get('content-type') ?? ''
  if (contentType.includes('application/json')) {
    return (await response.json()) as T
  }
  return (await response.text()) as unknown as T
}

export const http = {
  get<T>(path: string, query?: QueryParams, signal?: AbortSignal): Promise<T> {
    return request<T>(path, { query, signal })
  },
  post<T>(path: string, body?: unknown, query?: QueryParams, signal?: AbortSignal): Promise<T> {
    return request<T>(path, { method: 'POST', body, query, signal })
  },
}

/** 把任意异常转成可展示的中文描述。 */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.isTimeout) return `${error.message}，请稍后重试。`
    if (error.isNetwork) return `${error.message}。请确认后端已启动（uvicorn 默认 8000 端口）后重试。`
    if (error.isUnauthorized) return `${error.message}。请检查访问权限或 Webhook Token 配置。`
    if (error.isNotFound) return `${error.message}。`
    if (error.isServerError) return `${error.message}。可稍后重试或查看后端日志。`
    return error.message
  }
  if (error instanceof Error) return error.message
  return String(error)
}

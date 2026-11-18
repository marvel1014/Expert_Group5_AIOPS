import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, buildUrl, describeError, request } from './client'

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('buildUrl', () => {
  it('跳过空查询参数', () => {
    expect(buildUrl('/api/v1/alerts', { host: 'db', service: '', page: 1, status: null })).toBe(
      '/api/v1/alerts?host=db&page=1',
    )
  })

  it('无参数时保持原路径', () => {
    expect(buildUrl('/api/v1/alerts')).toBe('/api/v1/alerts')
  })
})

describe('request', () => {
  it('POST 时序列化 JSON 并解析成功响应', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }))

    const result = await request<{ ok: boolean }>('/api/v1/test', {
      method: 'POST',
      body: { a: 1 },
    })

    expect(result).toEqual({ ok: true })
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/v1/test')
    expect(init.method).toBe('POST')
    expect(init.body).toBe(JSON.stringify({ a: 1 }))
    expect((init.headers as Record<string, string>)['Content-Type']).toBe('application/json')
  })

  it('解析统一错误体 {error:{code,message,details}}', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { error: { code: 'validation_error', message: '请求参数校验失败', details: [{ field: 'x' }] } },
        422,
      ),
    )

    const error = (await request('/api/v1/test').catch((err: unknown) => err)) as ApiError
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(422)
    expect(error.code).toBe('validation_error')
    expect(error.message).toBe('请求参数校验失败')
    expect(error.details).toEqual([{ field: 'x' }])
  })

  it('404 给出资源不存在提示', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: 'Not Found' }, 404))

    const error = (await request('/api/v1/test').catch((err: unknown) => err)) as ApiError
    expect(error.isNotFound).toBe(true)
    expect(describeError(error)).toContain('资源不存在')
  })

  it('网络失败转为可重试 ApiError 且连接状态置为异常', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))

    const error = (await request('/api/v1/test').catch((err: unknown) => err)) as ApiError
    expect(error).toBeInstanceOf(ApiError)
    expect(error.isNetwork).toBe(true)
    expect(describeError(error)).toContain('无法连接后端服务')
  })

  it('204 无响应体返回 undefined', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
    await expect(request('/api/v1/test', { method: 'DELETE' })).resolves.toBeUndefined()
  })
})

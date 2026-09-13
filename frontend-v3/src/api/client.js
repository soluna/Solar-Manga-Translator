/**
 * API client for Solar Manga Translator backend (FastAPI).
 *
 * - Base URL: window.mangaDesktop?.runtime?.apiBaseUrl (desktop shell)
 *   → import.meta.env.VITE_API_BASE_URL → same-origin Vite proxy
 * - Auth: Bearer token (desktop shell / VITE_API_TOKEN / localStorage `solar-v3-api-token`)
 * - WebSocket auth: subprotocol `manga-translator` + `auth.<token>`
 */

const TOKEN_STORAGE_KEY = 'solar-v3-api-token'
export const isMockMode = import.meta.env?.VITE_MOCK_API === '1'

function resolveRuntimeValue() {
  if (typeof window !== 'undefined' && window.mangaDesktop?.runtime) {
    return {
      baseUrl: String(window.mangaDesktop.runtime.apiBaseUrl || '').trim(),
      token: String(window.mangaDesktop.runtime.apiToken || '').trim(),
    }
  }
  return { baseUrl: '', token: '' }
}

function readStoredToken() {
  try {
    return String(window.localStorage.getItem(TOKEN_STORAGE_KEY) || '').trim()
  } catch {
    return ''
  }
}

export function storeApiToken(token) {
  try {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, String(token || '').trim())
  } catch {
    /* ignore */
  }
}

const runtime = resolveRuntimeValue()
// 默认相对路径：走同源（Vite dev 代理 / mock / 后端静态托管皆可）；
// 桌面壳（mangaDesktop）或显式 VITE_API_BASE_URL 时才用绝对地址。
export const apiBaseUrl = String(
  runtime.baseUrl
  || import.meta.env?.VITE_API_BASE_URL
  || '',
).replace(/\/$/, '')

export const apiAccessToken = (runtime.token || import.meta.env?.VITE_API_TOKEN || readStoredToken()).trim()

/** Convert a path like /api/projects to an absolute URL. */
export function toApiUrl(path) {
  const value = String(path || '')
  if (/^https?:\/\//i.test(value)) return value
  return `${apiBaseUrl}${value.startsWith('/') ? value : `/${value}`}`
}

/** Append a query param without clobbering existing ones. */
export function withUrlQueryParam(url, key, value) {
  if (!url || value === undefined || value === null || value === '') {
    return url || ''
  }
  try {
    const origin = typeof window !== 'undefined' && /^https?:/.test(window.location?.origin || '')
      ? window.location.origin : apiBaseUrl || 'http://localhost'
    const parsedUrl = new URL(url, origin)
    parsedUrl.searchParams.set(key, String(value))
    return parsedUrl.toString()
  } catch {
    return url
  }
}

/** Append max_side=<n> to image endpoints for cheaper previews. */
export function withImagePreviewSize(url, maxSide) {
  const normalizedMaxSide = Number(maxSide || 0)
  if (!url || !Number.isFinite(normalizedMaxSide) || normalizedMaxSide <= 0) {
    return url || ''
  }
  return withUrlQueryParam(url, 'max_side', Math.round(normalizedMaxSide))
}

/** Bust the browser cache for mutable image artifacts. */
export function withCacheBust(url, revision = Date.now()) {
  if (!url) {
    return ''
  }
  return withUrlQueryParam(url, '_', revision)
}

/** fetch() with the local API Bearer token attached. */
export function apiFetch(input, init = {}) {
  const headers = new Headers(init.headers || {})
  if (apiAccessToken) {
    headers.set('Authorization', `Bearer ${apiAccessToken}`)
  }
  const fetchRequest = typeof window !== 'undefined' ? window.fetch.bind(window) : globalThis.fetch
  return fetchRequest(toApiUrl(input), { ...init, headers })
}

/** Response → JSON; throws readable errors on failure. */
export async function readApiJson(response, fallbackMessage) {
  const rawText = await response.text()
  if (!rawText) {
    return {}
  }
  try {
    return JSON.parse(rawText)
  } catch {
    if (!response.ok) {
      throw new Error(rawText.slice(0, 240) || fallbackMessage)
    }
    throw new Error(`${fallbackMessage}：后端返回了无法解析的响应。`)
  }
}

/** Read the error detail from a failed response (FastAPI style). */
export async function readApiError(response, fallbackMessage) {
  try {
    const body = await readApiJson(response, fallbackMessage)
    if (body?.detail) {
      if (typeof body.detail === 'string') {
        return body.detail
      }
      if (typeof body.detail === 'object' && body.detail.message) {
        return body.detail.message
      }
    }
    return fallbackMessage
  } catch {
    return fallbackMessage
  }
}

/** Open a WebSocket with the local API token (subprotocol auth). */
export function createApiWebSocket(path) {
  if (isMockMode) throw Object.assign(new Error('演示模式不执行后台任务，请连接真实后端。'), { code: 'MOCK_READ_ONLY' })
  if (!apiAccessToken) {
    return new WebSocket(toWebSocketUrl(path))
  }
  return new WebSocket(toWebSocketUrl(path), ['manga-translator', `auth.${apiAccessToken}`])
}

export function toWebSocketUrl(path) {
  const base = apiBaseUrl || (typeof window !== 'undefined' ? window.location.origin : 'http://localhost')
  const url = new URL(toApiUrl(path), base)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

// ---- typed helpers -------------------------------------------------------

function jsonHeaders(extra = {}) {
  return {
    'Content-Type': 'application/json',
    ...extra,
  }
}

export async function apiGetJson(path, fallbackMessage = '请求失败') {
  const response = await apiFetch(toApiUrl(path))
  if (!response.ok) {
    throw new Error(await readApiError(response, fallbackMessage))
  }
  return readApiJson(response, fallbackMessage)
}

export async function apiPostJson(path, payload, fallbackMessage = '提交失败') {
  const response = await apiFetch(toApiUrl(path), {
    method: 'POST',
    headers: jsonHeaders(),
    body: JSON.stringify(payload ?? {}),
  })
  if (!response.ok) {
    throw new Error(await readApiError(response, fallbackMessage))
  }
  return readApiJson(response, fallbackMessage)
}

export async function apiPatchJson(path, payload, fallbackMessage = '更新失败') {
  const response = await apiFetch(toApiUrl(path), {
    method: 'PATCH',
    headers: jsonHeaders(),
    body: JSON.stringify(payload ?? {}),
  })
  if (!response.ok) {
    throw new Error(await readApiError(response, fallbackMessage))
  }
  return readApiJson(response, fallbackMessage)
}

export async function apiPutJson(path, payload, fallbackMessage = '保存失败') {
  const response = await apiFetch(toApiUrl(path), {
    method: 'PUT',
    headers: jsonHeaders(),
    body: JSON.stringify(payload ?? {}),
  })
  if (!response.ok) {
    throw new Error(await readApiError(response, fallbackMessage))
  }
  return readApiJson(response, fallbackMessage)
}

export async function apiDeleteJson(path, fallbackMessage = '删除失败') {
  const response = await apiFetch(toApiUrl(path), { method: 'DELETE' })
  if (!response.ok) {
    throw new Error(await readApiError(response, fallbackMessage))
  }
  return readApiJson(response, fallbackMessage)
}

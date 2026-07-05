// Thin fetch wrapper: injects the JWT, handles 401 (refresh once) + errors.
// All API calls go through here. Calls are same-origin (nginx proxies /v1 -> api),
// so no base URL is needed; requests target relative paths like "/v1/numbers".

const TOKEN_KEY = 'cante.access_token'
const REFRESH_KEY = 'cante.refresh_token'

export function getAccessToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}
export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY)
}
export function setTokens(access: string, refresh: string) {
  localStorage.setItem(TOKEN_KEY, access)
  localStorage.setItem(REFRESH_KEY, refresh)
}
export function clearTokens() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(REFRESH_KEY)
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

async function rawFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  if (!headers.has('Content-Type') && init.body) headers.set('Content-Type', 'application/json')
  const token = getAccessToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  return fetch(path, { ...init, headers })
}

async function refreshOnce(): Promise<boolean> {
  const refresh = getRefreshToken()
  if (!refresh) return false
  try {
    const resp = await fetch('/v1/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refresh }),
    })
    if (!resp.ok) return false
    const data = await resp.json()
    setTokens(data.access_token, data.refresh_token)
    return true
  } catch {
    return false
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  let resp = await rawFetch(path, init)
  if (resp.status === 401) {
    const refreshed = await refreshOnce()
    if (refreshed) resp = await rawFetch(path, init)
    if (!resp.ok) {
      clearTokens()
      throw new ApiError(resp.status, 'Unauthorized')
    }
  }
  if (!resp.ok) {
    let msg = `${resp.status}`
    try {
      const data = await resp.json()
      msg = data.detail || data.message || JSON.stringify(data)
    } catch {
      msg = resp.statusText || msg
    }
    throw new ApiError(resp.status, msg)
  }
  if (resp.status === 204) return undefined as unknown as T
  return (await resp.json()) as T
}

export function api(method: string, path: string, body?: unknown): RequestInit {
  return {
    method,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  }
}

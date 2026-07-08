import { apiFetch, api, setTokens, clearTokens, getAccessToken } from './client'
import type { TokenPair, Principal, Paginated, UserRow } from './types'

export async function login(email: string, password: string): Promise<TokenPair> {
  const data = await apiFetch<TokenPair>('/v1/auth/login', api('POST', '/v1/auth/login', { email, password }))
  setTokens(data.access_token, data.refresh_token)
  return data
}

export async function fetchMe(): Promise<Principal> {
  return apiFetch<Principal>('/v1/auth/me', { method: 'GET' })
}

export async function updateMe(patch: { language_ui?: string }): Promise<Principal> {
  return apiFetch<Principal>('/v1/auth/me', api('PATCH', '/v1/auth/me', patch))
}

export function logout() {
  clearTokens()
}

export function isLoggedIn(): boolean {
  return !!getAccessToken()
}

// ── Admin user management ──────────────────────────────────────────

export function listUsers(cursor = ''): Promise<Paginated<UserRow>> {
  const q = cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''
  return apiFetch<Paginated<UserRow>>(`/v1/auth/users${q}`)
}

export function createUser(body: { email: string; password: string; role: string }): Promise<{ id: string; email: string; role: string }> {
  return apiFetch('/v1/auth/users', api('POST', '/v1/auth/users', body))
}

export function updateUser(id: string, patch: { role?: string; language_ui?: string; email?: string }): Promise<void> {
  return apiFetch(`/v1/auth/users/${id}`, api('PATCH', `/v1/auth/users/${id}`, patch))
}

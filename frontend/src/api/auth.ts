import { apiFetch, api, setTokens, clearTokens, getAccessToken } from './client'
import type { TokenPair, Principal } from './types'

export async function login(email: string, password: string): Promise<TokenPair> {
  const data = await apiFetch<TokenPair>('/v1/auth/login', api('POST', '/v1/auth/login', { email, password }))
  setTokens(data.access_token, data.refresh_token)
  return data
}

export async function fetchMe(): Promise<Principal> {
  return apiFetch<Principal>('/v1/auth/me', { method: 'GET' })
}

export function logout() {
  clearTokens()
}

export function isLoggedIn(): boolean {
  return !!getAccessToken()
}

import { apiFetch, api } from './client'
import type { Number, QRResult, Paginated } from './types'

export interface NumberCreate {
  phone: string
  display_name?: string
  channel_type?: string
}

export async function listNumbers(cursor = ''): Promise<Paginated<Number>> {
  const q = cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''
  return apiFetch<Paginated<Number>>(`/v1/numbers${q}`)
}

export async function createNumber(input: NumberCreate): Promise<Number> {
  return apiFetch<Number>('/v1/numbers', api('POST', '/v1/numbers', input))
}

export async function getQR(id: string): Promise<QRResult> {
  return apiFetch<QRResult>(`/v1/numbers/${id}/qr`)
}

export async function connectNumber(id: string): Promise<unknown> {
  return apiFetch(`/v1/numbers/${id}/connect`, { method: 'POST' })
}

export async function disconnectNumber(id: string): Promise<unknown> {
  return apiFetch(`/v1/numbers/${id}/disconnect`, { method: 'POST' })
}

export async function deleteNumber(id: string): Promise<unknown> {
  return apiFetch(`/v1/numbers/${id}`, { method: 'DELETE' })
}

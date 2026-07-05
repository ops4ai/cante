import { apiFetch, api } from './client'
import type { Paginated, Conversation, ConversationDetail } from './types'

export interface ConvListParams {
  cursor?: string
  state?: string
  bot_id?: string
  number_id?: string
}

export async function listConversations(params: ConvListParams = {}): Promise<Paginated<Conversation>> {
  const q = new URLSearchParams()
  if (params.cursor) q.set('cursor', params.cursor)
  if (params.state) q.set('state', params.state)
  if (params.bot_id) q.set('bot_id', params.bot_id)
  if (params.number_id) q.set('number_id', params.number_id)
  const qs = q.toString()
  return apiFetch<Paginated<Conversation>>(`/v1/conversations${qs ? `?${qs}` : ''}`)
}

export async function getConversation(id: string): Promise<ConversationDetail> {
  return apiFetch<ConversationDetail>(`/v1/conversations/${id}`)
}

export async function takeoverConversation(id: string): Promise<unknown> {
  return apiFetch(`/v1/conversations/${id}/takeover`, { method: 'POST' })
}

export async function closeConversation(id: string): Promise<unknown> {
  return apiFetch(`/v1/conversations/${id}/close`, { method: 'POST' })
}

export async function sendAsHuman(id: string, body: string): Promise<unknown> {
  return apiFetch(`/v1/conversations/${id}/send`, api('POST', `/v1/conversations/${id}/send`, { body }))
}

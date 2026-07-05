import { apiFetch, api } from './client'
import type { Paginated, Bot, Skill, Provider, Route, Contact } from './types'

// Generic list+create+patch+delete over the cursor-paginated /v1 endpoints.
function list<T>(base: string) {
  return async (cursor = ''): Promise<Paginated<T>> => {
    const q = cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''
    return apiFetch<Paginated<T>>(`${base}${q}`)
  }
}
function create<T>(base: string) {
  return (body: unknown) => apiFetch<T>(base, api('POST', base, body))
}
function patch<T>(base: string) {
  return (id: string, body: unknown) => apiFetch<T>(`${base}/${id}`, api('PATCH', `${base}/${id}`, body))
}
function del(base: string) {
  return (id: string) => apiFetch(`${base}/${id}`, { method: 'DELETE' })
}

export const listBots = list<Bot>('/v1/bots'); export const createBot = create<Bot>('/v1/bots'); export const patchBot = patch<Bot>('/v1/bots')
export const listSkills = list<Skill>('/v1/skills'); export const createSkill = create<Skill>('/v1/skills'); export const patchSkill = patch<Skill>('/v1/skills')
export const listProviders = list<Provider>('/v1/providers'); export const createProvider = create<Provider>('/v1/providers'); export const patchProvider = patch<Provider>('/v1/providers')
export const listRoutes = list<Route>('/v1/routes'); export const createRoute = create<Route>('/v1/routes'); export const deleteRoute = del('/v1/routes')
export const listContacts = list<Contact>('/v1/contacts'); export const patchContact = patch<Contact>('/v1/contacts')

export async function testProvider(id: string): Promise<unknown> {
  return apiFetch(`/v1/providers/${id}/test`, { method: 'POST' })
}

import { CrudPage, asCode } from '../components/CrudPage'
import type { Column } from '../components/Table'
import type { Bot, Skill, Provider, Route, Contact } from '../api/types'
import { listBots, createBot, patchBot, listSkills, createSkill, patchSkill, listProviders, createProvider, patchProvider, listRoutes, createRoute, listContacts, patchContact } from '../api/crud'
import { apiFetch } from '../api/client'
import { useAuth } from '../auth/AuthContext'

function useIsAdmin() {
  const { principal } = useAuth()
  return principal?.role === 'admin'
}

export function BotsPage() {
  const isAdmin = useIsAdmin()
  const columns: Column<Bot>[] = [
    { key: 'name', header: 'Name', render: (b) => b.name },
    { key: 'type', header: 'Type', render: (b) => asCode(b.type_label) },
    { key: 'lang', header: 'Language', render: (b) => asCode(b.language_default) },
    { key: 'enabled', header: 'Enabled', render: (b) => (b.enabled ? '✓' : '✗') },
    { key: 'skill', header: 'Skill', render: (b) => asCode(b.skill_id?.slice(0, 8)) },
    { key: 'provider', header: 'Provider', render: (b) => asCode(b.provider_id?.slice(0, 8)) },
  ]
  return (
    <CrudPage<Bot>
      title="Bots"
      subtitle="Agent configurations (Skill + Provider + language)"
      queryKey="bots"
      list={listBots}
      create={isAdmin ? createBot : undefined}
      patch={isAdmin ? patchBot : undefined}
      canWrite={isAdmin}
      columns={columns}
      fields={[
        { name: 'name', label: 'Name', required: true },
        { name: 'skill_id', label: 'Skill ID', required: true, placeholder: 'paste skill id' },
        { name: 'provider_id', label: 'Provider ID', required: true, placeholder: 'paste provider id' },
        { name: 'type_label', label: 'Type label', default: 'custom' },
        { name: 'language_default', label: 'Default language', default: 'en' },
        { name: 'enabled', label: 'Enabled', type: 'select', default: 'true', options: [{ value: 'true', label: 'Yes' }, { value: 'false', label: 'No' }] },
      ]}
    />
  )
}

export function SkillsPage() {
  const isAdmin = useIsAdmin()
  const columns: Column<Skill>[] = [
    { key: 'name', header: 'Name', render: (s) => s.name },
    { key: 'preset', header: 'Preset', render: (s) => asCode(s.preset) },
    { key: 'lang', header: 'Language', render: (s) => asCode(s.language_default) },
  ]
  return (
    <CrudPage<Skill>
      title="Skills"
      subtitle="Markdown playbooks that define bot behavior"
      queryKey="skills"
      list={listSkills}
      create={isAdmin ? createSkill : undefined}
      patch={isAdmin ? patchSkill : undefined}
      canWrite={isAdmin}
      columns={columns}
      fields={[
        { name: 'name', label: 'Name', required: true },
        { name: 'preset', label: 'Preset', default: 'custom' },
        { name: 'language_default', label: 'Default language', default: 'en' },
        { name: 'playbook_md', label: 'Playbook (Markdown)', type: 'textarea', placeholder: 'You are a helpful…' },
        { name: 'guardrails_md', label: 'Guardrails (Markdown)', type: 'textarea' },
        { name: 'done_condition', label: 'Done condition' },
        { name: 'scope', label: 'Scope (JSON)', type: 'json' },
        { name: 'tools', label: 'Tools (JSON)', type: 'json' },
      ]}
    />
  )
}

export function ProvidersPage() {
  const isAdmin = useIsAdmin()
  const columns: Column<Provider>[] = [
    { key: 'name', header: 'Name', render: (p) => p.name },
    { key: 'type', header: 'Type', render: (p) => asCode(p.type) },
    { key: 'model', header: 'Model', render: (p) => asCode(p.model) },
    { key: 'base', header: 'Base URL', render: (p) => asCode(p.base_url) },
  ]
  return (
    <CrudPage<Provider>
      title="Providers"
      subtitle="LLM endpoints (model + URL + API key reference)"
      queryKey="providers"
      list={listProviders}
      create={isAdmin ? createProvider : undefined}
      patch={isAdmin ? patchProvider : undefined}
      canWrite={isAdmin}
      columns={columns}
      fields={[
        { name: 'name', label: 'Name', required: true, placeholder: 'DeepSeek' },
        { name: 'type', label: 'Type', required: true, placeholder: 'anthropic | openai_compatible' },
        { name: 'base_url', label: 'Base URL', required: true, placeholder: 'https://api.deepseek.com/anthropic/v1' },
        { name: 'model', label: 'Model', required: true, placeholder: 'deepseek-v4-pro' },
        { name: 'api_key_ref', label: 'API key env var name', required: true, placeholder: 'DEEPSEEK_API_KEY' },
        { name: 'params', label: 'Params (JSON)', type: 'json' },
      ]}
    />
  )
}

export function RoutesPage() {
  const isAdmin = useIsAdmin()
  const columns: Column<Route>[] = [
    { key: 'number', header: 'Number', render: (r) => asCode(r.number_id?.slice(0, 8)) },
    { key: 'bot', header: 'Bot', render: (r) => asCode(r.bot_id?.slice(0, 8)) },
    { key: 'selector', header: 'Selector', render: (r) => asCode(r.selector) },
    { key: 'priority', header: 'Priority', render: (r) => r.priority },
  ]
  return (
    <CrudPage<Route>
      title="Routes"
      subtitle="Connect a Number to a Bot"
      queryKey="routes"
      list={listRoutes}
      create={isAdmin ? createRoute : undefined}
      canWrite={isAdmin}
      columns={columns}
      fields={[
        { name: 'number_id', label: 'Number ID', required: true },
        { name: 'bot_id', label: 'Bot ID', required: true },
        { name: 'selector', label: 'Selector', default: 'default' },
        { name: 'selector_value', label: 'Selector value' },
        { name: 'priority', label: 'Priority', type: 'number', default: 0 },
      ]}
    />
  )
}

export function ContactsPage() {
  const isAdmin = useIsAdmin()
  const columns: Column<Contact>[] = [
    { key: 'phone', header: 'Phone', render: (c) => asCode(c.phone) },
    { key: 'name', header: 'Name', render: (c) => c.name || '—' },
  ]
  return (
    <CrudPage<Contact>
      title="Contacts"
      subtitle="People who have messaged your numbers"
      queryKey="contacts"
      list={listContacts}
      patch={isAdmin ? patchContact : undefined}
      canWrite={isAdmin}
      columns={columns}
      fields={[
        { name: 'name', label: 'Name' },
        { name: 'attributes', label: 'Attributes (JSON)', type: 'json' },
      ]}
    />
  )
}

export function GroupsPage() {
  // Groups use a separate endpoint shape (members); this is a read-only list for now.
  return (
    <CrudPage
      title="Groups"
      subtitle="Contact groups (read-only list for now)"
      queryKey="groups"
      list={async () => {
        const d = await apiFetch<{ items: { id: string; name: string }[]; next_cursor: string | null }>('/v1/groups')
        return d
      }}
      columns={[
        { key: 'name', header: 'Name', render: (g: { id: string; name: string }) => g.name },
      ]}
    />
  )
}

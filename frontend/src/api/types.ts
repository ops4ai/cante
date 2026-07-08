// TS types mirroring the cante API Pydantic models + response shapes.

export interface TokenPair {
  access_token: string
  refresh_token: string
}

export interface Principal {
  user_id: string
  tenant_id: string
  role: string
  email?: string
  language_ui?: string
}

export interface UserRow {
  id: string
  email: string
  role: string
  language_ui: string
  created_at: string
}

export interface Paginated<T> {
  items: T[]
  next_cursor: string | null
}

export interface Number {
  id: string
  phone: string
  display_name: string
  channel_type: string
  status?: string
  connection_config?: Record<string, unknown>
  tenant_id: string
}

export interface QRResult {
  qr_code: string // base64 (data-uri ready)
  status: string
}

export interface Bot {
  id: string
  name: string
  skill_id: string
  provider_id: string
  type_label: string
  language_default: string
  enabled: boolean
  tenant_id: string
}

export interface Skill {
  id: string
  name: string
  preset: string
  playbook_md: string
  guardrails_md: string
  language_default: string
  scope: Record<string, unknown>
  tools: Record<string, unknown>
  done_condition: string
  escalation: Record<string, unknown>
  tenant_id: string
}

export interface Provider {
  id: string
  name: string
  type: string
  base_url: string
  model: string
  api_key_ref: string
  params: Record<string, unknown>
  tenant_id: string
}

export interface Route {
  id: string
  number_id: string
  bot_id: string
  selector: string
  selector_value: string
  priority: number
  tenant_id: string
}

export interface Contact {
  id: string
  phone: string
  name: string
  attributes?: Record<string, unknown>
  status?: string  // "active" | "blocked"
  tenant_id: string
}

export interface Conversation {
  id: string
  state: string
  language_detected?: string
  contact_id: string
  bot_id: string
  number_id: string
  last_activity_at: string
  started_at: string
  bot_name?: string
  number_phone?: string
  contact_phone?: string
}

export interface Message {
  id: string
  direction: string
  role: string
  body: string
  created_at: string
}

export interface ConversationDetail {
  id: string
  state: string
  context_json: Record<string, unknown> | null
  messages: Message[]
}

export interface Learning {
  id: string
  type: string
  suggestion_md: string
  category: string
  status: string
  diagnosis: string
  suggestion_type: string
  confidence: number
  conversation_id: string
  created_at: string
}

export interface LearningStats {
  total: number
  pending: number
  approved: number
  rejected: number
  snoozed: number
  by_category: Record<string, number>
  by_type: Record<string, number>
}

export interface MetricsOverview {
  [key: string]: number | string
}

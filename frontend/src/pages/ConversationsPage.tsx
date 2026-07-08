import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { useQuery } from 'react-query'
import { PageHeader } from '../components/PageHeader'
import { Table, Column } from '../components/Table'
import { Spinner, ErrorState, EmptyState } from '../components/Spinner'
import { listConversations } from '../api/conversations'
import type { Conversation, Paginated } from '../api/types'

const STATE_BADGE: Record<string, string> = {
  bot: 'bg-green-100 text-green-700',
  human: 'bg-amber-100 text-amber-700',
  closed: 'bg-gray-100 text-gray-500',
  escalated: 'bg-red-100 text-red-700',
}

export function ConversationsPage() {
  const { t } = useTranslation()
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const [rows, setRows] = useState<Conversation[]>([])
  const [next, setNext] = useState<string | null>(null)
  const [state, setState] = useState('')

  const { isLoading, isError, error, refetch } = useQuery<Paginated<Conversation>, Error>(
    ['conversations', cursor, state],
    () => listConversations({ cursor, state: state || undefined }),
    {
      keepPreviousData: true,
      refetchInterval: 10_000, // refresh the list every 10s
      onSuccess: (data) => {
        setRows((prev) => (cursor ? [...prev, ...data.items] : data.items))
        setNext(data.next_cursor ?? null)
      },
    },
  )

  const cols: Column<Conversation>[] = [
    { key: 'number', header: t('conversations.col.number'), render: (c) => <code className="text-xs">{c.number_phone || c.number_id?.slice(0, 8) || '—'}</code> },
    { key: 'bot', header: t('conversations.col.bot'), render: (c) => <span className="text-sm">{c.bot_name || c.bot_id?.slice(0, 8) || '—'}</span> },
    { key: 'contact', header: t('conversations.col.contact'), render: (c) => <Link to={`/conversations/${c.id}`} className="font-mono text-xs text-brand-600 hover:underline">{c.contact_phone || c.contact_id.slice(0, 8)}</Link> },
    { key: 'state', header: t('conversations.col.state'), render: (c) => <span className={`rounded px-1.5 py-0.5 text-[10px] uppercase ${STATE_BADGE[c.state] ?? 'bg-gray-100 text-gray-600'}`}>{c.state}</span> },
    { key: 'last', header: t('conversations.col.last'), render: (c) => <span className="text-xs text-gray-500">{c.last_activity_at}</span> },
  ]

  return (
    <div>
      <PageHeader
        title={t('conversations.title')}
        subtitle={t('conversations.subtitle')}
        action={
          <select className="rounded-md border border-gray-300 px-2 py-1 text-sm" value={state} onChange={(e) => { setState(e.target.value); setCursor(undefined); setRows([]) }}>
            <option value="">{t('conversations.all_states')}</option>
            <option value="bot">{t('conversations.state_bot')}</option>
            <option value="human">{t('conversations.state_human')}</option>
            <option value="escalated">{t('conversations.state_escalated')}</option>
            <option value="closed">{t('conversations.state_closed')}</option>
          </select>
        }
      />
      {isError && <ErrorState message={(error as Error)?.message ?? t('common.failed_load')} />}
      {isLoading && rows.length === 0 && <Spinner />}
      {!isLoading && rows.length === 0 && <EmptyState message={t('conversations.empty')} />}
      {rows.length > 0 && (
        <Table columns={cols} rows={rows} loading={isLoading} nextCursor={next} onLoadMore={() => next && setCursor(next)} />
      )}
      <div className="mt-2 text-xs text-gray-400">
        <button className="hover:underline" onClick={() => refetch()}>{t('conversations.refresh')}</button>
      </div>
    </div>
  )
}

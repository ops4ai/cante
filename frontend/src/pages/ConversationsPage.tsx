import { useState } from 'react'
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
    {
      key: 'contact',
      header: 'Contact',
      render: (c) => <Link to={`/conversations/${c.id}`} className="font-mono text-xs text-brand-600 hover:underline">{c.contact_id.slice(0, 8)}</Link>,
    },
    {
      key: 'state',
      header: 'State',
      render: (c) => <span className={`rounded px-1.5 py-0.5 text-[10px] uppercase ${STATE_BADGE[c.state] ?? 'bg-gray-100 text-gray-600'}`}>{c.state}</span>,
    },
    { key: 'bot', header: 'Bot', render: (c) => <code className="text-xs">{c.bot_id?.slice(0, 8) ?? '—'}</code> },
    { key: 'last', header: 'Last activity', render: (c) => <span className="text-xs text-gray-500">{c.last_activity_at}</span> },
  ]

  return (
    <div>
      <PageHeader
        title="Conversations"
        subtitle="Live conversations across your numbers (auto-refreshes)"
        action={
          <select className="rounded-md border border-gray-300 px-2 py-1 text-sm" value={state} onChange={(e) => { setState(e.target.value); setCursor(undefined); setRows([]) }}>
            <option value="">All states</option>
            <option value="bot">Bot</option>
            <option value="human">Human</option>
            <option value="escalated">Escalated</option>
            <option value="closed">Closed</option>
          </select>
        }
      />
      {isError && <ErrorState message={(error as Error)?.message ?? 'Failed to load conversations'} />}
      {isLoading && rows.length === 0 && <Spinner />}
      {!isLoading && rows.length === 0 && <EmptyState message="No conversations yet. Send a WhatsApp message to a connected number." />}
      {rows.length > 0 && (
        <Table columns={cols} rows={rows} loading={isLoading} nextCursor={next} onLoadMore={() => next && setCursor(next)} />
      )}
      <div className="mt-2 text-xs text-gray-400">
        <button className="hover:underline" onClick={() => refetch()}>Refresh now</button>
      </div>
    </div>
  )
}

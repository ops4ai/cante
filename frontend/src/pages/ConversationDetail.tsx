import { FormEvent, useState, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from 'react-query'
import { PageHeader } from '../components/PageHeader'
import { Spinner, ErrorState } from '../components/Spinner'
import { Button, inputCls } from '../components/Modal'
import { getConversation, takeoverConversation, closeConversation, sendAsHuman } from '../api/conversations'

export function ConversationDetail() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [draft, setDraft] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)

  const { data, isLoading, isError, error } = useQuery(
    ['conversation', id],
    () => getConversation(id),
    // Poll every 3s for live messages — this is the "watch it happen" view.
    { refetchInterval: 3_000, refetchIntervalInBackground: false },
  )

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [data?.messages.length])

  const takeoverMut = useMutation(() => takeoverConversation(id), { onSuccess: () => qc.invalidateQueries(['conversation', id]) })
  const closeMut = useMutation(() => closeConversation(id), { onSuccess: () => qc.invalidateQueries(['conversation', id]) })
  const sendMut = useMutation((body: string) => sendAsHuman(id, body), {
    onSuccess: () => { setDraft(''); qc.invalidateQueries(['conversation', id]) },
  })

  const onSend = (e: FormEvent) => { e.preventDefault(); if (draft.trim()) sendMut.mutate(draft.trim()) }

  const isHuman = data?.state === 'human'
  const isClosed = data?.state === 'closed'

  return (
    <div>
      <PageHeader
        title={`Conversation ${id.slice(0, 8)}`}
        subtitle={data ? `State: ${data.state}` : undefined}
        action={
          <div className="flex gap-2">
            {data && !isHuman && !isClosed && <Button variant="ghost" onClick={() => takeoverMut.mutate()}>Take over</Button>}
            {data && !isClosed && <Button variant="ghost" onClick={() => closeMut.mutate()}>Close</Button>}
            <Button variant="ghost" onClick={() => navigate('/conversations')}>Back</Button>
          </div>
        }
      />
      {isLoading && <Spinner />}
      {isError && <ErrorState message={(error as Error)?.message ?? 'Failed to load conversation'} />}
      {data && (
        <div className="flex h-[70vh] flex-col">
          <div ref={scrollRef} className="flex-1 space-y-2 overflow-y-auto rounded-lg border border-gray-200 bg-white p-4">
            {data.messages.length === 0 && <div className="text-center text-sm text-gray-400">No messages yet.</div>}
            {data.messages.map((m) => {
              const inbound = m.direction === 'inbound'
              return (
                <div key={m.id} className={`flex ${inbound ? 'justify-start' : 'justify-end'}`}>
                  <div className={`max-w-[70%] rounded-lg px-3 py-2 text-sm ${inbound ? 'bg-gray-100 text-gray-800' : 'bg-brand-600 text-white'}`}>
                    <div className="whitespace-pre-wrap break-words">{m.body}</div>
                    <div className={`mt-1 text-[10px] ${inbound ? 'text-gray-400' : 'text-brand-100'}`}>{m.role} · {m.created_at}</div>
                  </div>
                </div>
              )
            })}
          </div>
          {isClosed ? (
            <div className="mt-3 rounded-md bg-gray-100 p-3 text-center text-sm text-gray-500">This conversation is closed.</div>
          ) : isHuman ? (
            <form onSubmit={onSend} className="mt-3 flex gap-2">
              <input className={inputCls} placeholder="Type as human…" value={draft} onChange={(e) => setDraft(e.target.value)} disabled={sendMut.isLoading} />
              <Button type="submit" disabled={!draft.trim() || sendMut.isLoading}>{sendMut.isLoading ? 'Sending…' : 'Send'}</Button>
            </form>
          ) : (
            <div className="mt-3 rounded-md bg-amber-50 p-3 text-center text-xs text-amber-700">
              Bot is handling this conversation. Click <strong>Take over</strong> to reply as a human.
            </div>
          )}
          {sendMut.error && <div className="mt-2 text-xs text-red-600">{String(sendMut.error)}</div>}
        </div>
      )}
    </div>
  )
}

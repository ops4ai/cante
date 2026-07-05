import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from 'react-query'
import { PageHeader } from '../components/PageHeader'
import { Table, Column } from '../components/Table'
import { Button, Field, inputCls, Modal } from '../components/Modal'
import { QRModal } from '../components/QRModal'
import { Spinner, ErrorState, EmptyState } from '../components/Spinner'
import { listNumbers, createNumber, connectNumber, disconnectNumber } from '../api/numbers'
import type { Number, Paginated } from '../api/types'
import { useAuth } from '../auth/AuthContext'

export function NumbersPage() {
  const { principal } = useAuth()
  const isAdmin = principal?.role === 'admin'
  const qc = useQueryClient()
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const [rows, setRows] = useState<Number[]>([])
  const [next, setNext] = useState<string | null>(null)
  const [qrFor, setQrFor] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)

  const { isLoading, isError, error } = useQuery<Paginated<Number>, Error>(
    ['numbers', cursor],
    () => listNumbers(cursor ?? ''),
    {
      keepPreviousData: true,
      onSuccess: (data) => {
        setRows((prev) => (cursor ? [...prev, ...data.items] : data.items))
        setNext(data.next_cursor ?? null)
      },
    },
  )

  const createMut = useMutation(createNumber, {
    onSuccess: () => { qc.invalidateQueries('numbers'); setShowCreate(false) },
  })

  const connectMut = useMutation((id: string) => connectNumber(id), {
    onSuccess: () => qc.invalidateQueries('numbers'),
  })
  const disconnectMut = useMutation((id: string) => disconnectNumber(id), {
    onSuccess: () => qc.invalidateQueries('numbers'),
  })

  const cols: Column<Number>[] = [
    { key: 'phone', header: 'Phone', render: (n) => <span className="font-mono text-xs">{n.phone}</span> },
    { key: 'name', header: 'Display name', render: (n) => n.display_name || '—' },
    { key: 'channel', header: 'Channel', render: (n) => <code className="text-xs">{n.channel_type}</code> },
    {
      key: 'actions',
      header: 'Actions',
      render: (n) => (
        <div className="flex gap-2">
          <Button variant="ghost" onClick={() => setQrFor(n.id)}>QR</Button>
          {isAdmin && (
            <Button variant="ghost" disabled={connectMut.isLoading} onClick={() => connectMut.mutate(n.id)}>Connect</Button>
          )}
          {isAdmin && (
            <Button variant="ghost" disabled={disconnectMut.isLoading} onClick={() => disconnectMut.mutate(n.id)}>Disconnect</Button>
          )}
        </div>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title="Numbers"
        subtitle="WhatsApp numbers connected to this instance"
        action={isAdmin && <Button onClick={() => setShowCreate(true)}>+ New number</Button>}
      />
      {isError && <ErrorState message={(error as Error)?.message ?? 'Failed to load numbers'} />}
      {isLoading && rows.length === 0 && <Spinner />}
      {!isLoading && rows.length === 0 && <EmptyState message="No numbers yet. Create one and connect it by QR." />}
      {rows.length > 0 && (
        <Table columns={cols} rows={rows} loading={isLoading} nextCursor={next} onLoadMore={() => next && setCursor(next)} />
      )}

      {showCreate && (
        <CreateNumberModal
          onClose={() => setShowCreate(false)}
          creating={createMut.isLoading}
          onCreate={(v) => createMut.mutate(v)}
          error={createMut.error ? String(createMut.error) : null}
        />
      )}
      {qrFor && <QRModal numberId={qrFor} onClose={() => setQrFor(null)} onConnected={() => qc.invalidateQueries('numbers')} />}
    </div>
  )
}

function CreateNumberModal({ onClose, onCreate, creating, error }: {
  onClose: () => void
  onCreate: (v: { phone: string; display_name?: string }) => void
  creating: boolean
  error: string | null
}) {
  const [phone, setPhone] = useState('')
  const [name, setName] = useState('')
  return (
    <Modal open onClose={onClose} title="New number">
      <div className="space-y-3">
        <Field label="Phone (E.164, e.g. +351900000000)">
          <input className={inputCls} value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+351…" />
        </Field>
        <Field label="Display name (optional)">
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        {error && <div className="rounded bg-red-50 p-2 text-xs text-red-700">{error}</div>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button disabled={!phone || creating} onClick={() => onCreate({ phone, display_name: name || undefined })}>
            {creating ? 'Creating…' : 'Create'}
          </Button>
        </div>
      </div>
    </Modal>
  )
}

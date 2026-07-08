import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from 'react-query'
import { PageHeader } from '../components/PageHeader'
import { Table, Column } from '../components/Table'
import { Button, Field, inputCls, Modal } from '../components/Modal'
import { QRModal } from '../components/QRModal'
import { Spinner, ErrorState, EmptyState } from '../components/Spinner'
import { listNumbers, createNumber, disconnectNumber, deleteNumber } from '../api/numbers'
import type { Number, Paginated } from '../api/types'
import { useAuth } from '../auth/AuthContext'

export function NumbersPage() {
  const { t } = useTranslation()
  const { principal } = useAuth()
  const isAdmin = principal?.role === 'admin'
  const qc = useQueryClient()
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const [rows, setRows] = useState<Number[]>([])
  const [next, setNext] = useState<string | null>(null)
  const [qrFor, setQrFor] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<Number | null>(null)

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

  const deleteMut = useMutation((id: string) => deleteNumber(id), {
    onSuccess: () => { qc.invalidateQueries('numbers'); setConfirmDelete(null) },
  })
  const disconnectMut = useMutation((id: string) => disconnectNumber(id), {
    onSuccess: () => qc.invalidateQueries('numbers'),
  })

  const cols: Column<Number>[] = [
    { key: 'phone', header: t('numbers.phone'), render: (n) => <span className="font-mono text-xs">{n.phone}</span> },
    { key: 'name', header: t('numbers.display_name'), render: (n) => n.display_name || '—' },
    { key: 'channel', header: t('numbers.channel'), render: (n) => <code className="text-xs">{n.channel_type}</code> },
    {
      key: 'status',
      header: t('numbers.status'),
      render: (n) => {
        const connected = n.status === 'connected'
        return (
          <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${connected ? 'text-green-700' : 'text-red-600'}`}>
            <span className={`inline-block h-2 w-2 rounded-full ${connected ? 'bg-green-500' : 'bg-red-500'}`} />
            {connected ? t('numbers.connected') : t('numbers.disconnected')}
          </span>
        )
      },
    },
    {
      key: 'actions',
      header: t('numbers.actions'),
      render: (n) => (
        <div className="flex gap-2">
          {n.status === 'connected' ? (
            isAdmin && (
              <Button variant="ghost" onClick={() => disconnectMut.mutate(n.id)} disabled={disconnectMut.isLoading}>
                {t('numbers.disconnect')}
              </Button>
            )
          ) : (
            <Button variant="primary" onClick={() => setQrFor(n.id)}>{t('numbers.connect')}</Button>
          )}
          {isAdmin && (
            <Button variant="danger" onClick={() => setConfirmDelete(n)} disabled={deleteMut.isLoading}>
              {t('common.delete')}
            </Button>
          )}
        </div>
      ),
    },
  ]

  return (
    <div>
      <PageHeader
        title={t('numbers.title')}
        subtitle={t('numbers.subtitle')}
        action={isAdmin && <Button onClick={() => setShowCreate(true)}>+ {t('numbers.new_number')}</Button>}
      />
      {isError && <ErrorState message={(error as Error)?.message ?? t('common.failed_load')} />}
      {isLoading && rows.length === 0 && <Spinner />}
      {!isLoading && rows.length === 0 && <EmptyState message={t('numbers.empty')} />}
      {rows.length > 0 && (
        <Table columns={cols} rows={rows} loading={isLoading} nextCursor={next} onLoadMore={() => next && setCursor(next)} />
      )}

      {(deleteMut.error || disconnectMut.error) && (
        <div className="mt-4 rounded bg-red-50 p-3 text-sm text-red-700">
          {deleteMut.error ? String(deleteMut.error) : String(disconnectMut.error)}
        </div>
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
      {confirmDelete && (
        <Modal open onClose={() => setConfirmDelete(null)} title={t('common.delete')}>
          <p className="text-sm text-gray-600">
            {t('numbers.confirm_delete')}
            <br />
            <span className="font-mono text-xs">{confirmDelete.phone}</span>
            {confirmDelete.display_name ? ` — ${confirmDelete.display_name}` : ''}
          </p>
          {deleteMut.error && (
            <div className="mt-2 rounded bg-red-50 p-2 text-xs text-red-700">{String(deleteMut.error)}</div>
          )}
          <div className="mt-4 flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setConfirmDelete(null)}>{t('common.cancel')}</Button>
            <Button variant="danger" disabled={deleteMut.isLoading} onClick={() => deleteMut.mutate(confirmDelete.id)}>
              {deleteMut.isLoading ? t('common.saving') : t('common.delete')}
            </Button>
          </div>
        </Modal>
      )}
    </div>
  )
}

function CreateNumberModal({ onClose, onCreate, creating, error }: {
  onClose: () => void
  onCreate: (v: { phone: string; display_name?: string }) => void
  creating: boolean
  error: string | null
}) {
  const { t } = useTranslation()
  const [phone, setPhone] = useState('')
  const [name, setName] = useState('')
  return (
    <Modal open onClose={onClose} title={t('numbers.new_number')}>
      <div className="space-y-3">
        <Field label={t('numbers.phone_placeholder')}>
          <input className={inputCls} value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+351…" />
        </Field>
        <Field label={t('numbers.display_name')}>
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        {error && <div className="rounded bg-red-50 p-2 text-xs text-red-700">{error}</div>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>{t('common.cancel')}</Button>
          <Button disabled={!phone || creating} onClick={() => onCreate({ phone, display_name: name || undefined })}>
            {creating ? t('common.creating') : t('numbers.create_number')}
          </Button>
        </div>
      </div>
    </Modal>
  )
}
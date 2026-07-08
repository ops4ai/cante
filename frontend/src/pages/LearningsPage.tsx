import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from 'react-query'
import { PageHeader } from '../components/PageHeader'
import { Table, Column } from '../components/Table'
import { Button, Modal } from '../components/Modal'
import { Spinner, ErrorState, EmptyState } from '../components/Spinner'
import { listLearnings, approveLearning, rejectLearning, snoozeLearning, getLearningStats, runAnalysis } from '../api/crud'
import type { Learning, Paginated, LearningStats } from '../api/types'
import { useAuth } from '../auth/AuthContext'

const CONFIDENCE_BADGE: Record<string, string> = {
  high: 'bg-green-100 text-green-700',
  mid: 'bg-amber-100 text-amber-700',
  low: 'bg-red-100 text-red-700',
}
const STATUS_BADGE: Record<string, string> = {
  pending: 'bg-amber-100 text-amber-700',
  approved: 'bg-green-100 text-green-700',
  rejected: 'bg-red-100 text-red-700',
  snoozed: 'bg-gray-100 text-gray-500',
}

function confidenceLabel(c: number): string { return c >= 0.7 ? 'high' : c >= 0.4 ? 'mid' : 'low' }

export function LearningsPage() {
  const { t } = useTranslation()
  const { principal } = useAuth()
  const isAdmin = principal?.role === 'admin'
  const qc = useQueryClient()
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const [rows, setRows] = useState<Learning[]>([])
  const [next, setNext] = useState<string | null>(null)
  const [status, setStatus] = useState('')
  const [detail, setDetail] = useState<Learning | null>(null)

  const { isLoading, isError, error } = useQuery<Paginated<Learning>, Error>(
    ['learnings', cursor, status],
    () => listLearnings(cursor ?? ''),
    {
      keepPreviousData: true,
      onSuccess: (data) => {
        setRows((prev) => (cursor ? [...prev, ...data.items] : data.items))
        setNext(data.next_cursor ?? null)
      },
    },
  )

  const { data: stats } = useQuery<LearningStats, Error>('learnings-stats', getLearningStats, { refetchInterval: 30_000 })

  const approveMut = useMutation(approveLearning, { onSuccess: () => qc.invalidateQueries('learnings') })
  const rejectMut = useMutation(rejectLearning, { onSuccess: () => qc.invalidateQueries('learnings') })
  const snoozeMut = useMutation(snoozeLearning, { onSuccess: () => qc.invalidateQueries('learnings') })
  const analyzeMut = useMutation(runAnalysis, { onSuccess: () => { qc.invalidateQueries('learnings'); qc.invalidateQueries('learnings-stats') } })

  const cols: Column<Learning>[] = [
    {
      key: 'category',
      header: 'Category',
      render: (l) => <span className="text-xs font-medium">{l.category || '—'}</span>,
    },
    {
      key: 'type',
      header: 'Type',
      render: (l) => <span className="text-xs">{l.suggestion_type || '—'}</span>,
    },
    {
      key: 'confidence',
      header: 'Conf.',
      render: (l) => {
        const label = confidenceLabel(l.confidence ?? 0)
        return <span className={`rounded px-1.5 py-0.5 text-[10px] uppercase ${CONFIDENCE_BADGE[label]}`}>{(l.confidence ?? 0).toFixed(2)}</span>
      },
    },
    {
      key: 'status',
      header: 'Status',
      render: (l) => <span className={`rounded px-1.5 py-0.5 text-[10px] uppercase ${STATUS_BADGE[l.status] ?? 'bg-gray-100 text-gray-600'}`}>{l.status}</span>,
    },
    {
      key: 'suggestion',
      header: 'Suggestion',
      render: (l) => (
        <button className="text-left text-xs text-brand-600 hover:underline max-w-xs truncate block" onClick={() => setDetail(l)}>
          {l.suggestion_md?.slice(0, 80) || '—'}
        </button>
      ),
    },
    { key: 'created', header: 'Created', render: (l) => <span className="text-xs text-gray-500">{l.created_at}</span> },
    {
      key: 'actions',
      header: '',
      render: (l) => {
        if (l.status !== 'pending') return null
        return (
          <div className="flex gap-1">
            <Button variant="ghost" className="text-green-600 text-xs" onClick={() => approveMut.mutate(l.id)} title="Approve">✓</Button>
            <Button variant="ghost" className="text-red-600 text-xs" onClick={() => rejectMut.mutate(l.id)} title="Reject">✕</Button>
            <Button variant="ghost" className="text-gray-400 text-xs" onClick={() => snoozeMut.mutate(l.id)} title="Snooze">⏳</Button>
          </div>
        )
      },
    },
  ]

  return (
    <div>
      <PageHeader
        title={t('learnings.title')}
        subtitle={t('learnings.subtitle')}
        action={
          <div className="flex gap-2">
            <select className="rounded-md border border-gray-300 px-2 py-1 text-sm" value={status} onChange={(e) => { setStatus(e.target.value); setCursor(undefined); setRows([]) }}>
              <option value="">All statuses</option>
              <option value="pending">{t('learnings.pending')}</option>
              <option value="approved">{t('learnings.approved')}</option>
              <option value="rejected">{t('learnings.rejected')}</option>
              <option value="snoozed">{t('learnings.snoozed')}</option>
            </select>
            {isAdmin && (
              <Button variant="ghost" disabled={analyzeMut.isLoading} onClick={() => analyzeMut.mutate()}>
                {analyzeMut.isLoading ? t('learnings.running') : t('learnings.run_analysis')}
              </Button>
            )}
          </div>
        }
      />

      {/* Stats cards */}
      {stats && (
        <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-5">
          {[
            { label: t('learnings.total'), value: stats.total, color: 'text-gray-800' },
            { label: t('learnings.pending'), value: stats.pending, color: 'text-amber-600' },
            { label: t('learnings.approved'), value: stats.approved, color: 'text-green-600' },
            { label: t('learnings.rejected'), value: stats.rejected, color: 'text-red-600' },
            { label: t('learnings.snoozed'), value: stats.snoozed, color: 'text-gray-500' },
          ].map((s) => (
            <div key={s.label} className="rounded-lg border border-gray-200 bg-white p-3">
              <div className="text-xs uppercase text-gray-400">{s.label}</div>
              <div className={`mt-1 text-2xl font-semibold ${s.color}`}>{s.value}</div>
            </div>
          ))}
        </div>
      )}

      {isError && <ErrorState message={(error as Error)?.message ?? 'Failed to load learnings'} />}
      {isLoading && rows.length === 0 && <Spinner />}
      {!isLoading && rows.length === 0 && <EmptyState message={t('learnings.empty')} />}
      {rows.length > 0 && (
        <Table columns={cols} rows={rows} loading={isLoading} nextCursor={next} onLoadMore={() => next && setCursor(next)} />
      )}

      {/* Detail modal */}
      {detail && (
        <Modal open onClose={() => setDetail(null)} title={`${t('learnings.detail_title')} — ${detail.category || 'other'}`}>
          <div className="space-y-3 text-sm">
            <div>
              <span className="text-xs font-medium text-gray-500">Diagnosis</span>
              <p className="mt-0.5 text-gray-800 whitespace-pre-wrap">{detail.diagnosis || '—'}</p>
            </div>
            <div>
              <span className="text-xs font-medium text-gray-500">Suggestion ({detail.suggestion_type})</span>
              <p className="mt-0.5 text-gray-800 whitespace-pre-wrap">{detail.suggestion_md || '—'}</p>
            </div>
            <div className="flex gap-4 text-xs text-gray-500">
              <span>Confidence: {(detail.confidence ?? 0).toFixed(2)}</span>
              <span>Status: {detail.status}</span>
            </div>
            {detail.status === 'pending' && (
              <div className="flex gap-2 pt-2">
                <Button variant="primary" onClick={() => { approveMut.mutate(detail.id); setDetail(null) }}>{t('learnings.approve_apply')}</Button>
                <Button variant="ghost" onClick={() => { rejectMut.mutate(detail.id); setDetail(null) }}>{t('learnings.reject')}</Button>
                <Button variant="ghost" onClick={() => { snoozeMut.mutate(detail.id); setDetail(null) }}>{t('learnings.snooze')}</Button>
              </div>
            )}
          </div>
        </Modal>
      )}
    </div>
  )
}

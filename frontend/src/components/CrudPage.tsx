import { ReactNode, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from 'react-query'
import { PageHeader } from './PageHeader'
import { Table, Column } from './Table'
import { Modal, Button, Field, inputCls } from './Modal'
import { Spinner, ErrorState, EmptyState } from './Spinner'
import { apiFetch } from '../api/client'

export type FieldType = 'text' | 'number' | 'textarea' | 'select' | 'json' | 'asyncSelect'
export interface FieldDef {
  name: string
  label: string
  type?: FieldType
  options?: { value: string; label: string }[]
  loadOptions?: () => Promise<{ value: string; label: string }[]>
  required?: boolean
  default?: string | number
  placeholder?: string
  showIf?: (values: Record<string, unknown>) => boolean
}

interface CrudConfig<T> {
  title: string
  subtitle: string
  queryKey: string
  list: (cursor: string) => Promise<{ items: T[]; next_cursor: string | null }>
  create?: (body: Record<string, unknown>) => Promise<T>
  patch?: (id: string, body: Record<string, unknown>) => Promise<T>
  fields: FieldDef[]
  columns: Column<T>[]
  canWrite?: boolean
}

export function CrudPage<T extends { id: string }>(cfg: CrudConfig<T>) {
  const qc = useQueryClient()
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const [rows, setRows] = useState<T[]>([])
  const [next, setNext] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [editRow, setEditRow] = useState<T | null>(null)

  const { isLoading, isError, error } = useQuery([cfg.queryKey, cursor], () => list(cfg, cursor), {
    keepPreviousData: true,
    onSuccess: (data) => {
      setRows((prev) => (cursor ? [...prev, ...data.items] : data.items))
      setNext(data.next_cursor ?? null)
    },
  })

  const createMut = useMutation(cfg.create ?? (() => Promise.reject()), {
    onSuccess: () => { qc.invalidateQueries(cfg.queryKey); setShowCreate(false) },
  })
  const patchMut = useMutation((args: { id: string; body: Record<string, unknown> }) => (cfg.patch ?? (() => Promise.reject()))(args.id, args.body), {
    onSuccess: () => { qc.invalidateQueries(cfg.queryKey); setEditRow(null) },
  })

  const canWrite = cfg.canWrite !== false && !!cfg.create
  const cols = cfg.patch
    ? [...cfg.columns, { key: '_edit', header: '', render: (r: T) => <Button variant="ghost" onClick={() => setEditRow(r)}>Edit</Button> } as Column<T>]
    : cfg.columns

  return (
    <div>
      <PageHeader
        title={cfg.title}
        subtitle={cfg.subtitle}
        action={canWrite && <Button onClick={() => setShowCreate(true)}>+ New</Button>}
      />
      {isError && <ErrorState message={(error as Error)?.message ?? 'Failed to load'} />}
      {isLoading && rows.length === 0 && <Spinner />}
      {!isLoading && rows.length === 0 && <EmptyState message={`No ${cfg.title.toLowerCase()} yet.`} />}
      {rows.length > 0 && (
        <Table columns={cols} rows={rows} loading={isLoading} nextCursor={next} onLoadMore={() => next && setCursor(next)} />
      )}

      {showCreate && (
        <FormModal
          title={`New ${cfg.title.slice(0, -1)}`}
          fields={cfg.fields}
          submitting={createMut.isLoading}
          error={createMut.error ? String(createMut.error) : null}
          onClose={() => setShowCreate(false)}
          onSubmit={(v) => createMut.mutate(v)}
        />
      )}
      {editRow && (
        <FormModal
          title={`Edit ${cfg.title.slice(0, -1)}`}
          fields={cfg.fields}
          initial={editRow as Record<string, unknown>}
          submitting={patchMut.isLoading}
          error={patchMut.error ? String(patchMut.error) : null}
          onClose={() => setEditRow(null)}
          onSubmit={(v) => patchMut.mutate({ id: editRow.id, body: v })}
        />
      )}
    </div>
  )
}

async function list<T extends { id: string }>(cfg: CrudConfig<T>, cursor: string) {
  return cfg.list(cursor)
}

function defaultValue(f: FieldDef): string | number {
  if (f.default !== undefined) return f.default
  if (f.type === 'number') return 0
  return ''
}

function FormModal({ title, fields, initial, onSubmit, onClose, submitting, error }: {
  title: string
  fields: FieldDef[]
  initial?: Record<string, unknown>
  onSubmit: (v: Record<string, unknown>) => void
  onClose: () => void
  submitting: boolean
  error: string | null
}) {
  const [values, setValues] = useState<Record<string, unknown>>(() => {
    const v: Record<string, unknown> = {}
    for (const f of fields) {
      const raw = initial?.[f.name] ?? defaultValue(f)
      // JSON fields: store as pretty-printed string for editing
      if (f.type === 'json' && raw !== null && typeof raw === 'object') {
        v[f.name] = JSON.stringify(raw, null, 2)
      } else {
        v[f.name] = raw
      }
    }
    return v
  })
  const set = (n: string, val: unknown) => setValues((p) => ({ ...p, [n]: val }))

  const onSubmitInner = () => {
    const out: Record<string, unknown> = {}
    for (const f of fields) {
      const raw = values[f.name]
      if (f.type === 'json' && typeof raw === 'string') {
        try { out[f.name] = JSON.parse(raw || '{}') } catch { out[f.name] = {} }
      } else if (f.type === 'number') {
        out[f.name] = Number(raw)
      } else if (raw === '' && !f.required) {
        // omit empty optional
      } else {
        out[f.name] = raw
      }
    }
    onSubmit(out)
  }

  return (
    <Modal open onClose={onClose} title={title}>
      <div className="space-y-3">
        {fields.filter((f) => !f.showIf || f.showIf(values)).map((f) => (
          <Field key={f.name} label={f.label}>
            {f.type === 'textarea' ? (
              <textarea className={`${inputCls} font-mono text-xs`} rows={20} style={{resize: 'vertical', minHeight: '16rem'}} value={String(values[f.name] ?? '')} placeholder={f.placeholder} onChange={(e) => set(f.name, e.target.value)} />
            ) : f.type === 'asyncSelect' && f.loadOptions ? (
              <AsyncSelect value={String(values[f.name] ?? '')} onChange={(v) => set(f.name, v)} loadOptions={f.loadOptions} queryKey={f.name} placeholder={f.placeholder} />
            ) : f.type === 'select' ? (
              <select className={inputCls} value={String(values[f.name] ?? '')} onChange={(e) => set(f.name, e.target.value)}>
                {f.options?.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            ) : f.type === 'json' ? (
              <textarea className={`${inputCls} font-mono text-xs`} rows={8} value={typeof values[f.name] === 'object' && values[f.name] !== null ? JSON.stringify(values[f.name], null, 2) : String(values[f.name] ?? '')} placeholder='{"key":"value"}' onChange={(e) => set(f.name, e.target.value)} />
            ) : (
              <input className={inputCls} type={f.type === 'number' ? 'number' : 'text'} value={String(values[f.name] ?? '')} placeholder={f.placeholder} onChange={(e) => set(f.name, f.type === 'number' ? Number(e.target.value) : e.target.value)} />
            )}
          </Field>
        ))}
        {error && <div className="rounded bg-red-50 p-2 text-xs text-red-700">{error}</div>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button disabled={submitting} onClick={onSubmitInner}>{submitting ? 'Saving…' : 'Save'}</Button>
        </div>
      </div>
    </Modal>
  )
}

export function asCode(v: ReactNode): ReactNode {
  return <code className="text-xs">{v}</code>
}

// AsyncSelect — fetches options from an API endpoint and renders a <select>.
// Caches per queryKey so options are shared across multiple form instances.
export function AsyncSelect({
  value, onChange, loadOptions, queryKey, placeholder,
}: {
  value: string; onChange: (v: string) => void; loadOptions: () => Promise<{ value: string; label: string }[]>
  queryKey: string; placeholder?: string
}) {
  const { data: options, isLoading, isError } = useQuery(
    ['select', queryKey],
    loadOptions,
    { staleTime: 60_000, refetchOnWindowFocus: false },
  )
  return (
    <div className="relative">
      <select className={inputCls} value={value} onChange={(e) => onChange(e.target.value)} disabled={isLoading || isError}>
        <option value="">{isLoading ? 'Loading…' : isError ? 'Failed to load' : placeholder || 'Select…'}</option>
        {(options ?? []).map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
      {isLoading && <span className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-gray-400">⏳</span>}
    </div>
  )
}

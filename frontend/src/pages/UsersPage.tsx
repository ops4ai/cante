import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from 'react-query'
import { PageHeader } from '../components/PageHeader'
import { Table, Column } from '../components/Table'
import { Button, Field, inputCls, Modal } from '../components/Modal'
import { Spinner, ErrorState, EmptyState } from '../components/Spinner'
import { listUsers, createUser, updateUser } from '../api/auth'
import type { UserRow, Paginated } from '../api/types'
import { useAuth } from '../auth/AuthContext'

const ROLES = [
  { value: 'admin', label: 'Admin' },
  { value: 'operator', label: 'Operator' },
]

const LANGS = [
  { value: 'en', label: 'English' },
  { value: 'pt', label: 'Português' },
  { value: 'es', label: 'Español' },
  { value: 'fr', label: 'Français' },
]

export function UsersPage() {
  const { t } = useTranslation()
  const { principal } = useAuth()
  const isAdmin = principal?.role === 'admin'
  const qc = useQueryClient()
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const [rows, setRows] = useState<UserRow[]>([])
  const [next, setNext] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [editRow, setEditRow] = useState<UserRow | null>(null)

  const { isLoading, isError, error } = useQuery<Paginated<UserRow>, Error>(
    ['users', cursor],
    () => listUsers(cursor ?? ''),
    {
      keepPreviousData: true,
      onSuccess: (data) => {
        setRows((prev) => (cursor ? [...prev, ...data.items] : data.items))
        setNext(data.next_cursor ?? null)
      },
    },
  )

  const cols: Column<UserRow>[] = [
    { key: 'email', header: t('users.col_email'), render: (u) => <span className="font-mono text-xs">{u.email}</span> },
    {
      key: 'role',
      header: t('users.col_role'),
      render: (u) => (
        <span className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${u.role === 'admin' ? 'bg-purple-100 text-purple-800' : 'bg-gray-100 text-gray-700'}`}>
          {u.role}
        </span>
      ),
    },
    {
      key: 'lang',
      header: t('users.col_lang'),
      render: (u) => <span className="text-xs">{LANGS.find((l) => l.value === u.language_ui)?.label || u.language_ui || 'en'}</span>,
    },
    {
      key: 'actions',
      header: '',
      render: (u) =>
        isAdmin && (
          <Button variant="ghost" onClick={() => setEditRow(u)}>{t('common.edit')}</Button>
        ),
    },
  ]

  return (
    <div>
      <PageHeader
        title={t('users.title')}
        subtitle={t('users.subtitle')}
        action={isAdmin && <Button onClick={() => setShowCreate(true)}>+ {t('users.new_user')}</Button>}
      />
      {isError && <ErrorState message={(error as Error)?.message ?? t('common.failed_load')} />}
      {isLoading && rows.length === 0 && <Spinner />}
      {!isLoading && rows.length === 0 && <EmptyState message={t('users.empty')} />}
      {rows.length > 0 && (
        <Table columns={cols} rows={rows} loading={isLoading} nextCursor={next} onLoadMore={() => next && setCursor(next)} />
      )}

      {showCreate && (
        <CreateUserModal
          onClose={() => setShowCreate(false)}
          onCreated={() => { qc.invalidateQueries('users'); setShowCreate(false) }}
        />
      )}
      {editRow && (
        <EditUserModal
          user={editRow}
          onClose={() => setEditRow(null)}
          onUpdated={() => { qc.invalidateQueries('users'); setEditRow(null) }}
        />
      )}
    </div>
  )
}

function CreateUserModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState('operator')
  const createMut = useMutation(createUser, { onSuccess: onCreated })

  return (
    <Modal open onClose={onClose} title={t('users.new_user')}>
      <div className="space-y-3">
        <Field label={t('users.field_email')}>
          <input className={inputCls} type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="user@example.com" />
        </Field>
        <Field label={t('users.field_password')}>
          <input className={inputCls} type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Field label={t('users.col_role')}>
          <select className={inputCls} value={role} onChange={(e) => setRole(e.target.value)}>
            {ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        </Field>
        {createMut.error && <div className="rounded bg-red-50 p-2 text-xs text-red-700">{String(createMut.error)}</div>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>{t('common.cancel')}</Button>
          <Button disabled={!email || !password || createMut.isLoading} onClick={() => createMut.mutate({ email, password, role })}>
            {createMut.isLoading ? t('common.creating') : t('common.create')}
          </Button>
        </div>
      </div>
    </Modal>
  )
}

function EditUserModal({ user, onClose, onUpdated }: { user: UserRow; onClose: () => void; onUpdated: () => void }) {
  const [role, setRole] = useState(user.role)
  const [lang, setLang] = useState(user.language_ui || 'en')
  const updateMut = useMutation(
    (patch: { role?: string; language_ui?: string }) => updateUser(user.id, patch),
    { onSuccess: onUpdated },
  )

  return (
    <Modal open onClose={onClose} title={`${t('common.edit')} ${user.email}`}>
      <div className="space-y-3">
        <Field label={t('users.col_role')}>
          <select className={inputCls} value={role} onChange={(e) => setRole(e.target.value)}>
            {ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        </Field>
        <Field label={t('users.col_lang')}>
          <select className={inputCls} value={lang} onChange={(e) => setLang(e.target.value)}>
            {LANGS.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
          </select>
        </Field>
        {updateMut.error && <div className="rounded bg-red-50 p-2 text-xs text-red-700">{String(updateMut.error)}</div>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>{t('common.cancel')}</Button>
          <Button disabled={updateMut.isLoading} onClick={() => updateMut.mutate({ role, language_ui: lang })}>
            {updateMut.isLoading ? t('common.saving') : t('common.save')}
          </Button>
        </div>
      </div>
    </Modal>
  )
}

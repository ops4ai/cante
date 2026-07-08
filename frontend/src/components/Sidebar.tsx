import { useEffect } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../auth/AuthContext'
import { updateMe } from '../api/auth'
import { useMutation } from 'react-query'

const LANGS: Record<string, string> = {
  en: 'English',
  pt: 'Português',
  es: 'Español',
  fr: 'Français',
}

export function Sidebar() {
  const { t, i18n } = useTranslation()
  const { principal, logout, refresh } = useAuth()
  const navigate = useNavigate()

  // Sync i18n language with stored user preference on load
  useEffect(() => {
    const stored = principal?.language_ui
    if (stored && stored !== i18n.language) {
      i18n.changeLanguage(stored)
    }
  }, [principal?.language_ui, i18n])

  const langMut = useMutation(
    (lang: string) => {
      i18n.changeLanguage(lang)
      return updateMe({ language_ui: lang })
    },
    { onSuccess: () => refresh() },
  )

  const items = [
    { to: '/', label: t('nav.dashboard'), end: true },
    { to: '/numbers', label: t('nav.numbers') },
    { to: '/conversations', label: t('nav.conversations') },
    { to: '/bots', label: t('nav.bots') },
    { to: '/skills', label: t('nav.skills') },
    { to: '/providers', label: t('nav.providers') },
    { to: '/routes', label: t('nav.routes') },
    { to: '/contacts', label: t('nav.contacts') },
    { to: '/groups', label: t('nav.groups') },
    { to: '/metrics', label: t('nav.metrics') },
    { to: '/learnings', label: t('nav.learnings') },
    { to: '/users', label: t('nav.users') },
  ]

  const linkCls = ({ isActive }: { isActive: boolean }) =>
    `block rounded-md px-3 py-2 text-sm font-medium ${
      isActive ? 'bg-brand-50 text-brand-700' : 'text-gray-600 hover:bg-gray-100'
    }`
  return (
    <aside className="flex h-full w-56 flex-col border-r border-gray-200 bg-white">
      <div className="flex items-center gap-2 px-4 py-4">
        <img src="/icon.svg" alt="Cante" className="h-8 w-8" />
        <div>
          <div className="text-lg font-bold text-brand-700">Cante</div>
          <div className="text-xs text-gray-400">backoffice</div>
        </div>
      </div>
      <nav className="flex-1 space-y-1 px-2">
        {items.map((it) => (
          <NavLink key={it.to} to={it.to} end={it.end} className={linkCls}>
            {it.label}
          </NavLink>
        ))}
      </nav>
      <div className="border-t border-gray-200 p-3 text-xs text-gray-500">
        <div className="truncate">{principal?.email ?? principal?.user_id ?? '—'}</div>
        <div className="mt-0.5 inline-block rounded bg-gray-100 px-1.5 py-0.5 text-[10px] uppercase">{principal?.role ?? ''}</div>
        <div className="mt-2">
          <select
            className="w-full rounded border border-gray-200 bg-white px-1 py-1 text-xs text-gray-600"
            value={i18n.language || 'en'}
            onChange={(e) => langMut.mutate(e.target.value)}
            disabled={langMut.isLoading}
          >
            {Object.entries(LANGS).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </div>
        <button
          className="mt-2 block text-xs text-gray-400 hover:text-red-600"
          onClick={() => { logout(); navigate('/login') }}
        >
          {t('common.sign_out')}
        </button>
      </div>
    </aside>
  )
}

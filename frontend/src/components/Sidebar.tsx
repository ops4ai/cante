import { NavLink, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

const items = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/numbers', label: 'Numbers' },
  { to: '/conversations', label: 'Conversations' },
  { to: '/bots', label: 'Bots' },
  { to: '/skills', label: 'Skills' },
  { to: '/providers', label: 'Providers' },
  { to: '/routes', label: 'Routes' },
  { to: '/contacts', label: 'Contacts' },
  { to: '/groups', label: 'Groups' },
]

export function Sidebar() {
  const { principal, logout } = useAuth()
  const navigate = useNavigate()
  const linkCls = ({ isActive }: { isActive: boolean }) =>
    `block rounded-md px-3 py-2 text-sm font-medium ${
      isActive ? 'bg-brand-50 text-brand-700' : 'text-gray-600 hover:bg-gray-100'
    }`
  return (
    <aside className="flex h-full w-56 flex-col border-r border-gray-200 bg-white">
      <div className="px-4 py-4">
        <div className="text-lg font-bold text-brand-700">Cante</div>
        <div className="text-xs text-gray-400">backoffice</div>
      </div>
      <nav className="flex-1 space-y-1 px-2">
        {items.map((it) => (
          <NavLink key={it.to} to={it.to} end={it.end} className={linkCls}>
            {it.label}
          </NavLink>
        ))}
      </nav>
      <div className="border-t border-gray-200 p-3 text-xs text-gray-500">
        <div className="truncate">{principal?.user_id ?? '—'}</div>
        <div className="mt-0.5 inline-block rounded bg-gray-100 px-1.5 py-0.5 text-[10px] uppercase">{principal?.role ?? ''}</div>
        <button
          className="mt-2 block text-xs text-gray-400 hover:text-red-600"
          onClick={() => { logout(); navigate('/login') }}
        >
          Sign out
        </button>
      </div>
    </aside>
  )
}

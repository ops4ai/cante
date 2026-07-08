import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from 'react-query'
import { PageHeader } from '../components/PageHeader'
import { Spinner, ErrorState } from '../components/Spinner'
import { apiFetch } from '../api/client'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  LineChart, Line, AreaChart, Area, Legend,
} from 'recharts'

interface MetricsOverview {
  success: boolean
  data: {
    period: { from: string; to: string }
    totals: Record<string, number>
    daily: Array<{
      date: string
      conversations_total: number
      conversations_escalated: number
      conversations_closed: number
      messages_in: number
      messages_out: number
      tokens_total: number
    }>
    percentiles: {
      first_reply_p50: number
      first_reply_p95: number
      resolution_p50: number
      resolution_p95: number
      avg_messages_per_conversation: number
    }
  }
}

function fmtSecs(s: number): string {
  if (s < 60) return `${Math.round(s)}s`
  if (s < 3600) return `${Math.round(s / 60)}m`
  return `${(s / 3600).toFixed(1)}h`
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

export function MetricsPage() {
  const { t } = useTranslation()
  const [days, setDays] = useState(7)

  const { data, isLoading, isError, error } = useQuery<MetricsOverview>(
    ['metrics', days],
    () => {
      const to = new Date().toISOString().slice(0, 10)
      const from = new Date(Date.now() - (days - 1) * 86400000).toISOString().slice(0, 10)
      return apiFetch<MetricsOverview>(`/v1/metrics/overview?from_date=${from}&to_date=${to}`)
    },
    { refetchInterval: 60_000, staleTime: 30_000 },
  )

  if (isLoading) return <Spinner />
  if (isError || !data?.success || !data.data) return <ErrorState message={isError ? String(error) : 'Failed to load metrics'} />

  const d = data.data
  const totals: Record<string, number> = d.totals || {}
  const percentiles = d.percentiles || {} as MetricsOverview['data']['percentiles']
  const daily = d.daily || []

  // Prepare chart data — shorten date labels
  const chartData = (daily || []).map((d) => ({
    ...d,
    label: String(d.date).slice(5), // "MM-DD"
  }))

  const cards = [
    { label: t('metrics.conversations'), value: totals.conversations_total || 0, color: '#2563eb' },
    { label: t('metrics.escalated'), value: totals.conversations_escalated || 0, color: '#dc2626' },
    { label: t('metrics.closed'), value: totals.conversations_closed || 0, color: '#16a34a' },
    { label: t('metrics.msgs_in'), value: totals.messages_in || 0, color: '#4f46e5' },
    { label: t('metrics.msgs_out'), value: totals.messages_out || 0, color: '#7c3aed' },
    { label: t('metrics.tokens'), value: fmtTokens(totals.tokens_total || 0), color: '#d97706' },
  ]

  const perfCards = [
    { label: t('metrics.reply_p50'), sub: t('metrics.median'), value: fmtSecs(percentiles.first_reply_p50), color: '#0891b2' },
    { label: t('metrics.reply_p95'), sub: t('metrics.slowest'), value: fmtSecs(percentiles.first_reply_p95), color: '#0e7490' },
    { label: t('metrics.res_p50'), sub: t('metrics.median'), value: fmtSecs(percentiles.resolution_p50), color: '#059669' },
    { label: t('metrics.res_p95'), sub: t('metrics.slowest'), value: fmtSecs(percentiles.resolution_p95), color: '#047857' },
    { label: t('metrics.avg_msgs'), sub: t('metrics.per_convo'), value: String(percentiles.avg_messages_per_conversation), color: '#9333ea' },
  ]

  return (
    <div>
      <PageHeader
        title="Metrics"
        subtitle={d.period ? `${d.period.from} → ${d.period.to}` : ''}
        action={
          <select
            className="rounded-md border border-gray-300 px-3 py-2 text-sm"
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
          >
            <option value={1}>{t('metrics.today')}</option>
            <option value={7}>{t('metrics.last7')}</option>
            <option value={30}>{t('metrics.last30')}</option>
          </select>
        }
      />

      {/* KPI cards row */}
      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        {cards.map((c) => (
          <div key={c.label} className="rounded-lg border border-gray-200 bg-white p-3 shadow-sm">
            <div className="text-[11px] font-medium uppercase tracking-wide text-gray-400">{c.label}</div>
            <div className="mt-1 text-xl font-bold" style={{ color: c.color }}>{c.value}</div>
          </div>
        ))}
      </div>

      {/* Charts row: Conversations + Messages */}
      <div className="mb-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Conversations — stacked bar */}
        <div className="rounded-lg border border-gray-200 bg-white p-4">
          <h3 className="mb-3 text-sm font-semibold text-gray-700">{t('metrics.chart_convs')}</h3>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis dataKey="label" tick={{ fontSize: 11 }} stroke="#94a3b8" />
              <YAxis tick={{ fontSize: 11 }} stroke="#94a3b8" allowDecimals={false} />
              <Tooltip />
              <Bar dataKey="conversations_closed" name={t('metrics.closed')} stackId="a" fill="#16a34a" radius={[0, 0, 0, 0]} />
              <Bar dataKey="conversations_escalated" name={t('metrics.escalated')} stackId="a" fill="#dc2626" radius={[0, 0, 0, 0]} />
              <Bar dataKey="conversations_active" name="Active" stackId="a" fill="#3b82f6" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Messages — area chart */}
        <div className="rounded-lg border border-gray-200 bg-white p-4">
          <h3 className="mb-3 text-sm font-semibold text-gray-700">{t('metrics.chart_msgs')}</h3>
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis dataKey="label" tick={{ fontSize: 11 }} stroke="#94a3b8" />
              <YAxis tick={{ fontSize: 11 }} stroke="#94a3b8" allowDecimals={false} />
              <Tooltip />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              <Area type="monotone" dataKey="messages_in" name={t('metrics.inbound')} stroke="#4f46e5" fill="#4f46e5" fillOpacity={0.15} strokeWidth={2} />
              <Area type="monotone" dataKey="messages_out" name={t('metrics.outbound')} stroke="#7c3aed" fill="#7c3aed" fillOpacity={0.15} strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Tokens — line chart (full width) */}
      <div className="mb-6 rounded-lg border border-gray-200 bg-white p-4">
        <h3 className="mb-3 text-sm font-semibold text-gray-700">{t('metrics.chart_tokens')}</h3>
        <ResponsiveContainer width="100%" height={180}>
          <LineChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
            <XAxis dataKey="label" tick={{ fontSize: 11 }} stroke="#94a3b8" />
            <YAxis tick={{ fontSize: 11 }} stroke="#94a3b8" tickFormatter={fmtTokens} />
            <Tooltip formatter={(v: number) => [fmtTokens(v), 'Tokens']} />
            <Line type="monotone" dataKey="tokens_total" name="Tokens" stroke="#d97706" strokeWidth={2} dot={{ r: 3 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* Performance percentiles */}
      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-5">
        {perfCards.map((p) => (
          <div key={p.label} className="rounded-lg border border-gray-200 bg-gray-50 p-3 text-center">
            <div className="text-[11px] font-medium uppercase tracking-wide text-gray-400">{p.label}</div>
            <div className="mt-1 text-lg font-bold" style={{ color: p.color }}>{p.value}</div>
            <div className="text-[10px] text-gray-400">{p.sub}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

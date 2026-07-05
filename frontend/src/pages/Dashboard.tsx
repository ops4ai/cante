import { useQuery } from 'react-query'
import { PageHeader } from '../components/PageHeader'
import { Spinner, ErrorState } from '../components/Spinner'
import { apiFetch } from '../api/client'
import type { MetricsOverview } from '../api/types'

export function Dashboard() {
  const health = useQuery('healthz', () => apiFetch<{ status: string }>('/healthz'), { staleTime: 15_000 })
  const metrics = useQuery('metrics', () => apiFetch<MetricsOverview>('/v1/metrics/overview'))

  return (
    <div>
      <PageHeader title="Dashboard" subtitle="Overview of your cante instance" />
      <div className="mb-4 inline-flex items-center gap-2 rounded-full bg-gray-100 px-3 py-1 text-xs">
        <span className={`h-2 w-2 rounded-full ${health.data?.status === 'ok' ? 'bg-green-500' : 'bg-red-500'}`} />
        API: {health.isLoading ? '…' : health.data?.status ?? 'down'}
      </div>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        {metrics.isLoading && <Spinner />}
        {metrics.isError && <ErrorState message="Failed to load metrics" />}
        {metrics.data &&
          Object.entries(metrics.data).map(([k, v]) => (
            <div key={k} className="rounded-lg border border-gray-200 bg-white p-4">
              <div className="text-xs uppercase text-gray-400">{k.replace(/_/g, ' ')}</div>
              <div className="mt-1 text-2xl font-semibold text-gray-800">{v}</div>
            </div>
          ))}
      </div>
    </div>
  )
}

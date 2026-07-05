import { ReactNode } from 'react'

export interface Column<T> {
  key: string
  header: string
  render: (row: T) => ReactNode
}

export function Table<T extends { id: string }>({
  columns,
  rows,
  loading,
  nextCursor,
  onLoadMore,
  empty,
}: {
  columns: Column<T>[]
  rows: T[]
  loading: boolean
  nextCursor?: string | null
  onLoadMore?: () => void
  empty?: ReactNode
}) {
  return (
    <div className="overflow-hidden rounded-lg border border-gray-200 bg-white">
      <table className="w-full text-left text-sm">
        <thead className="bg-gray-50 text-xs uppercase text-gray-500">
          <tr>
            {columns.map((c) => (
              <th key={c.key} className="px-4 py-2 font-medium">{c.header}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {rows.map((row) => (
            <tr key={row.id} className="hover:bg-gray-50">
              {columns.map((c) => (
                <td key={c.key} className="px-4 py-2 align-top">{c.render(row)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && !loading && (
        <div className="p-8 text-center text-sm text-gray-400">{empty ?? 'No data'}</div>
      )}
      {nextCursor && (
        <div className="border-t bg-gray-50 p-2 text-center">
          <button className="text-xs font-medium text-brand-600 hover:underline" onClick={onLoadMore}>
            {loading ? 'Loading…' : 'Load more'}
          </button>
        </div>
      )}
    </div>
  )
}

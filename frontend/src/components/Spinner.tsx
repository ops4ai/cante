export function Spinner({ fullPage }: { fullPage?: boolean }) {
  const cls = 'inline-block h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-brand-500'
  if (fullPage) {
    return (
      <div className="flex h-screen w-full items-center justify-center">
        <span className={cls} />
      </div>
    )
  }
  return <span className={cls} />
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700">
      {message}
    </div>
  )
}

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-md border border-dashed border-gray-300 bg-white p-8 text-center text-sm text-gray-500">
      {message}
    </div>
  )
}

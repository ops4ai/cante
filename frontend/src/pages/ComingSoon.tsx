import { PageHeader } from '../components/PageHeader'

export function ComingSoon({ name }: { name: string }) {
  return (
    <div>
      <PageHeader title={name} subtitle="This section is being built." />
      <div className="rounded-md border border-dashed border-gray-300 bg-white p-8 text-center text-sm text-gray-500">
        The {name} page is part of the backoffice and will be available soon.
      </div>
    </div>
  )
}

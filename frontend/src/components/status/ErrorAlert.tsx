import { describeError } from '../../api/errors'

export function ErrorAlert({ error }: { error: unknown }) {
  const { title, description } = describeError(error)

  return (
    <div
      role="alert"
      className="rounded-xl border border-red-300 bg-red-50 p-4 text-red-900 dark:border-red-800/70 dark:bg-red-950/40 dark:text-red-100"
    >
      <p className="font-semibold">{title}</p>
      <p className="mt-1 text-sm">{description}</p>
    </div>
  )
}

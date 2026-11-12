import { useId } from 'react'

import type { PredictionWarning } from '../../api/types'

export function WarningList({ warnings }: { warnings: PredictionWarning[] }) {
  const headingId = useId()

  if (warnings.length === 0) {
    return null
  }

  return (
    <section
      aria-labelledby={headingId}
      className="flex gap-3 rounded-xl border border-amber-300 bg-amber-50 p-4 text-amber-950 dark:border-amber-700/60 dark:bg-amber-950/40 dark:text-amber-100"
    >
      <svg
        aria-hidden="true"
        viewBox="0 0 24 24"
        className="mt-0.5 size-5 shrink-0 text-amber-500"
        fill="currentColor"
      >
        <path d="M12 2 1 21h22L12 2zm1 15h-2v2h2v-2zm0-7h-2v5h2v-5z" />
      </svg>
      <div className="space-y-1">
        <h3 id={headingId} className="font-semibold">
          Зверніть увагу
        </h3>
        <ul className="space-y-1 text-sm">
          {warnings.map((warning) => (
            <li key={warning.code}>{warning.message}</li>
          ))}
        </ul>
      </div>
    </section>
  )
}

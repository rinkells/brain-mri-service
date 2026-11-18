import { useId } from 'react'

import type { PredictionWarning } from '../../api/types'
import { WarningIcon } from './WarningIcon'

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
      <WarningIcon />
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

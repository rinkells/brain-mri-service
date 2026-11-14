import { WarningIcon } from './WarningIcon'

/** Shown above a result that came with the low_confidence warning. */
export function LowConfidenceNotice() {
  return (
    <section
      aria-label="Низька впевненість моделі"
      className="flex gap-3 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950 dark:border-amber-700/60 dark:bg-amber-950/40 dark:text-amber-100"
    >
      <WarningIcon />
      <p>
        <strong className="font-semibold">Модель недостатньо впевнена в цьому прогнозі.</strong>{' '}
        Зверніть увагу: це навчальний проєкт, а не медичний виріб.
      </p>
    </section>
  )
}

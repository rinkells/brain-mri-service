import type { ClassName, PredictionResponse } from '../../api/types'
import { CLASS_LABELS, CLASS_ORDER } from '../../lib/classes'
import { formatPercent } from '../../lib/format'

interface ProbabilityBarsProps {
  probabilities: PredictionResponse['probabilities']
  predicted: ClassName
}

export function ProbabilityBars({ probabilities, predicted }: ProbabilityBarsProps) {
  return (
    <ul className="space-y-3">
      {CLASS_ORDER.map((name) => {
        const value = probabilities[name] ?? 0
        const isPredicted = name === predicted
        return (
          <li key={name}>
            <div className="mb-1 flex justify-between text-sm">
              <span className={isPredicted ? 'font-semibold' : undefined}>
                {CLASS_LABELS[name]}
              </span>
              <span className="tabular-nums text-slate-600 dark:text-slate-300">
                {formatPercent(value)}
              </span>
            </div>
            <div
              role="meter"
              aria-label={CLASS_LABELS[name]}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(value * 1000) / 10}
              aria-valuetext={formatPercent(value)}
              className="h-2.5 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800"
            >
              <div
                className={`h-full rounded-full ${isPredicted ? 'bg-sky-600 dark:bg-sky-500' : 'bg-slate-400 dark:bg-slate-600'}`}
                style={{ width: `${Math.min(Math.max(value, 0), 1) * 100}%` }}
              />
            </div>
          </li>
        )
      })}
    </ul>
  )
}

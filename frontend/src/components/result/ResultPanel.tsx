import type { PredictionResponse } from '../../api/types'
import { CLASS_LABELS } from '../../lib/classes'
import { formatPercent } from '../../lib/format'
import { HeatmapView } from './HeatmapView'
import { ProbabilityBars } from './ProbabilityBars'
import { WarningList } from './WarningList'

interface ResultPanelProps {
  result: PredictionResponse
  scanUrl: string
}

export function ResultPanel({ result, scanUrl }: ResultPanelProps) {
  const predicted = result.predicted_class
  const confidence = result.probabilities[predicted] ?? 0

  return (
    <section aria-labelledby="result-heading" className="space-y-6">
      <div>
        <h2
          id="result-heading"
          className="text-sm font-medium tracking-wide text-slate-500 uppercase dark:text-slate-400"
        >
          Результат
        </h2>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">Передбачений клас</p>
        <p className="text-3xl font-semibold" data-testid="predicted-class">
          {CLASS_LABELS[predicted]}
        </p>
        <p className="text-sm text-slate-600 tabular-nums dark:text-slate-300">
          ймовірність {formatPercent(confidence)} · знімок {result.image_size.width}×
          {result.image_size.height}
        </p>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          {result.id
            ? 'Результат збережено в історії (без самого знімка).'
            : 'Результат не збережено в історії: історію вимкнено або база даних недоступна.'}
        </p>
      </div>

      <WarningList warnings={result.warnings} />

      <div>
        <h3 className="mb-3 font-medium">Ймовірності класів</h3>
        <ProbabilityBars probabilities={result.probabilities} predicted={predicted} />
      </div>

      <HeatmapView scanUrl={scanUrl} heatmapBase64={result.gradcam_png_base64} />
    </section>
  )
}

import type { PredictionResponse } from '../../api/types'
import { VIEW_HASH } from '../../hooks/useView'
import { CLASS_LABELS } from '../../lib/classes'
import { formatPercent } from '../../lib/format'
import { groupWarnings } from '../../lib/warnings'
import { HeatmapView } from './HeatmapView'
import { ImageSizeNote } from './ImageSizeNote'
import { LowConfidenceNotice } from './LowConfidenceNotice'
import { NotMriLikeNotice } from './NotMriLikeNotice'
import { ProbabilityBars } from './ProbabilityBars'
import { WarningList } from './WarningList'

interface ResultPanelProps {
  result: PredictionResponse
  scanUrl: string
  /** Whether a user was signed in when the scan was sent: only then is it saved. */
  signedInAtRequest?: boolean
  /** Whether a user is signed in now: a guest is offered to sign in. */
  signedInNow?: boolean
}

interface SavedNoteProps {
  saved: boolean
  signedInAtRequest: boolean
  signedInNow: boolean
}

function SavedNote({ saved, signedInAtRequest, signedInNow }: SavedNoteProps) {
  if (saved) {
    return <>Результат збережено в історії (без самого знімка).</>
  }
  if (signedInAtRequest) {
    return <>Результат не збережено в історії: історію вимкнено або база даних недоступна.</>
  }
  if (signedInNow) {
    // Sent as a guest, and the user signed in since: the reason is that, nothing else.
    return <>Результат не збережено: аналіз виконано без входу.</>
  }
  return (
    <>
      Результат не збережено.{' '}
      <a
        href={VIEW_HASH.login}
        className="font-medium text-sky-700 underline-offset-2 hover:underline dark:text-sky-400"
      >
        Увійдіть
      </a>
      , щоб зберігати історію.
    </>
  )
}

export function ResultPanel({
  result,
  scanUrl,
  signedInAtRequest = false,
  signedInNow = false,
}: ResultPanelProps) {
  const predicted = result.predicted_class
  const confidence = result.probabilities[predicted] ?? 0
  // Known codes have their own presentation; other codes go to the list.
  const warnings = groupWarnings(result.warnings)

  return (
    <section aria-labelledby="result-heading" className="space-y-6">
      {warnings.notMriLike && <NotMriLikeNotice message={warnings.notMriLike.message} />}
      {warnings.lowConfidence && <LowConfidenceNotice />}

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
          <SavedNote
            saved={Boolean(result.id)}
            signedInAtRequest={signedInAtRequest}
            signedInNow={signedInNow}
          />
        </p>
        {warnings.imageSize && <ImageSizeNote message={warnings.imageSize.message} />}
      </div>

      <WarningList warnings={warnings.other} />

      <div>
        <h3 className="mb-3 font-medium">Ймовірності класів</h3>
        <ProbabilityBars probabilities={result.probabilities} predicted={predicted} />
      </div>

      <HeatmapView scanUrl={scanUrl} heatmapBase64={result.gradcam_png_base64} />
    </section>
  )
}

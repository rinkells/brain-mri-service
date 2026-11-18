import type { PredictionWarning } from '../../api/types'
import { groupWarnings } from '../../lib/warnings'
import { InfoIcon } from '../result/InfoIcon'

const yellowBadge =
  'inline-flex rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-900 dark:bg-amber-900/50 dark:text-amber-100'

function messages(warnings: PredictionWarning[]) {
  return warnings.map((warning) => warning.message).join('\n')
}

/**
 * Warnings of a saved prediction. Yellow is kept for low confidence (and codes the
 * interface does not know yet); the common image size warning is a grey mark.
 * The full message is in the tooltip.
 */
export function WarningMarks({ warnings }: { warnings: PredictionWarning[] }) {
  if (warnings.length === 0) {
    return <span className="text-slate-500 dark:text-slate-400">Ні</span>
  }

  const { lowConfidence, imageSize, other } = groupWarnings(warnings)

  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      {lowConfidence && (
        <span title={lowConfidence.message} className={yellowBadge}>
          Низька впевненість
        </span>
      )}
      {other.length > 0 && (
        <span title={messages(other)} className={yellowBadge}>
          Інше
        </span>
      )}
      {imageSize && (
        <span
          title={imageSize.message}
          className="inline-flex items-center gap-1 text-xs text-slate-500 dark:text-slate-400"
        >
          <InfoIcon />
          Нетиповий розмір
        </span>
      )}
    </span>
  )
}

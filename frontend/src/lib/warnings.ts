import type { PredictionWarning } from '../api/types'

/** Added by the backend when the top-1 probability is below LOW_CONFIDENCE_THRESHOLD. */
export const LOW_CONFIDENCE = 'low_confidence'

export function hasLowConfidence(warnings: PredictionWarning[]): boolean {
  return warnings.some((warning) => warning.code === LOW_CONFIDENCE)
}

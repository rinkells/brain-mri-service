import type { PredictionWarning } from '../api/types'

/** Added by the backend when the top-1 probability is below LOW_CONFIDENCE_THRESHOLD. */
export const LOW_CONFIDENCE = 'low_confidence'
/** Added by the backend for scans that are not 512×512. */
export const UNEXPECTED_IMAGE_SIZE = 'unexpected_image_size'

export interface GroupedWarnings {
  /** Shown as a yellow block above the result. */
  lowConfidence: boolean
  /** Shown as a small grey note under the result. */
  imageSize: PredictionWarning | undefined
  /** Every other code, shown in the yellow list of warnings. */
  other: PredictionWarning[]
}

export function groupWarnings(warnings: PredictionWarning[]): GroupedWarnings {
  return {
    lowConfidence: warnings.some((warning) => warning.code === LOW_CONFIDENCE),
    imageSize: warnings.find((warning) => warning.code === UNEXPECTED_IMAGE_SIZE),
    other: warnings.filter(
      (warning) => warning.code !== LOW_CONFIDENCE && warning.code !== UNEXPECTED_IMAGE_SIZE,
    ),
  }
}

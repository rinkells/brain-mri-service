import type { PredictionWarning } from '../api/types'

/** Added by the backend when the top-1 probability is below LOW_CONFIDENCE_THRESHOLD. */
export const LOW_CONFIDENCE = 'low_confidence'
/** Added by the backend for scans that are not 512×512. */
export const UNEXPECTED_IMAGE_SIZE = 'unexpected_image_size'

/** Warnings by how they are shown: only low confidence and unknown codes are yellow. */
export interface GroupedWarnings {
  /** Yellow: a block above the result, a badge in history. */
  lowConfidence: PredictionWarning | undefined
  /** Grey: a small note under the result, a small mark in history. */
  imageSize: PredictionWarning | undefined
  /** Every other code, shown in yellow as before. */
  other: PredictionWarning[]
}

export function groupWarnings(warnings: PredictionWarning[]): GroupedWarnings {
  return {
    lowConfidence: warnings.find((warning) => warning.code === LOW_CONFIDENCE),
    imageSize: warnings.find((warning) => warning.code === UNEXPECTED_IMAGE_SIZE),
    other: warnings.filter(
      (warning) => warning.code !== LOW_CONFIDENCE && warning.code !== UNEXPECTED_IMAGE_SIZE,
    ),
  }
}

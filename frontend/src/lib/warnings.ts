import type { PredictionWarning } from '../api/types'

/** Added by the backend when the top-1 probability is below LOW_CONFIDENCE_THRESHOLD. */
export const LOW_CONFIDENCE = 'low_confidence'
/** Added by the backend for scans that are not 512×512. */
export const UNEXPECTED_IMAGE_SIZE = 'unexpected_image_size'
/** Added by the backend when the image does not look like an MRI scan at all. */
export const NOT_MRI_LIKE = 'not_mri_like'

const OWN_PLACE = new Set([LOW_CONFIDENCE, UNEXPECTED_IMAGE_SIZE, NOT_MRI_LIKE])

/** Warnings by how they are shown: only the image size is grey. */
export interface GroupedWarnings {
  /** Yellow: a block with the message above the result, a badge in history. */
  notMriLike: PredictionWarning | undefined
  /** Yellow: a block above the result, a badge in history. */
  lowConfidence: PredictionWarning | undefined
  /** Grey: a small note under the result, a small mark in history. */
  imageSize: PredictionWarning | undefined
  /** Every other code, shown in yellow as before. */
  other: PredictionWarning[]
}

export function groupWarnings(warnings: PredictionWarning[]): GroupedWarnings {
  return {
    notMriLike: warnings.find((warning) => warning.code === NOT_MRI_LIKE),
    lowConfidence: warnings.find((warning) => warning.code === LOW_CONFIDENCE),
    imageSize: warnings.find((warning) => warning.code === UNEXPECTED_IMAGE_SIZE),
    other: warnings.filter((warning) => !OWN_PLACE.has(warning.code)),
  }
}

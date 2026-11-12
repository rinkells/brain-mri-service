import { MAX_UPLOAD_BYTES } from './config'
import { formatMegabytes } from './format'

const ACCEPTED_TYPES: readonly string[] = ['image/jpeg', 'image/png']
const ACCEPTED_EXTENSIONS: readonly string[] = ['.jpg', '.jpeg', '.png']

/** Value for the `accept` attribute of the file input. */
export const ACCEPT_ATTRIBUTE = [...ACCEPTED_TYPES, ...ACCEPTED_EXTENSIONS].join(',')

function hasAcceptedType(file: File): boolean {
  if (file.type) {
    return ACCEPTED_TYPES.includes(file.type)
  }
  // Some systems give no MIME type; fall back to the file name.
  const name = file.name.toLowerCase()
  return ACCEPTED_EXTENSIONS.some((extension) => name.endsWith(extension))
}

/**
 * Check a file before it is sent. Returns a message for the user, or null if
 * the file looks fine. The backend still checks the real content.
 */
export function validateImageFile(file: File, maxBytes = MAX_UPLOAD_BYTES): string | null {
  if (!hasAcceptedType(file)) {
    return `Файл «${file.name}» не підходить: потрібен знімок у форматі JPEG або PNG.`
  }
  if (file.size === 0) {
    return `Файл «${file.name}» порожній.`
  }
  if (file.size > maxBytes) {
    return `Файл завеликий (${formatMegabytes(file.size)}). Максимум — ${formatMegabytes(maxBytes)}.`
  }
  return null
}

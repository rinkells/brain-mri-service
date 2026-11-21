const percentFormat = new Intl.NumberFormat('uk-UA', {
  style: 'percent',
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
})

const megabyteFormats = new Map<number, Intl.NumberFormat>()
const byteFormat = new Intl.NumberFormat('uk-UA')

/** 0.8044 -> "80,4 %" */
export function formatPercent(value: number): string {
  return percentFormat.format(value)
}

/** 12_345_678 -> "11,8 МБ"; up to `fractionDigits` decimals, trailing zeros dropped. */
export function formatMegabytes(bytes: number, fractionDigits = 1): string {
  let format = megabyteFormats.get(fractionDigits)
  if (!format) {
    format = new Intl.NumberFormat('uk-UA', { maximumFractionDigits: fractionDigits })
    megabyteFormats.set(fractionDigits, format)
  }
  return `${format.format(bytes / 1024 / 1024)} МБ`
}

/**
 * Size of a chosen file: kilobytes below 1 MB, megabytes from there on.
 * 14_336 -> "14 КБ", 2_411_725 -> "2,3 МБ". A non-empty file is at least "1 КБ", never "0".
 */
export function formatFileSize(bytes: number): string {
  const kilobytes = Math.round(bytes / 1024)
  if (kilobytes >= 1024) {
    return formatMegabytes(bytes)
  }
  // A whole number from 0 to 1023: no decimal separator or digit grouping to format.
  return `${bytes > 0 ? Math.max(kilobytes, 1) : 0} КБ`
}

/**
 * Size of a file that is over the limit, never written the same as the limit:
 * 10_490_000 bytes over 10 MB -> "10,004 МБ", not "10 МБ". Up to three decimals,
 * then the exact number of bytes.
 */
export function formatSizeOverLimit(bytes: number, limitBytes: number): string {
  const size = formatFileSize(bytes)
  if (size !== formatMegabytes(limitBytes)) {
    return size
  }
  for (const digits of [2, 3]) {
    const precise = formatMegabytes(bytes, digits)
    if (precise !== formatMegabytes(limitBytes, digits)) {
      return precise
    }
  }
  return `${byteFormat.format(bytes)} Б`
}

const dateTimeFormat = new Intl.DateTimeFormat('uk-UA', { dateStyle: 'short', timeStyle: 'medium' })

/** ISO timestamp from the API (UTC) -> local date and time, e.g. "12.11.26, 23:42:29". */
export function formatDateTime(iso: string): string {
  return dateTimeFormat.format(new Date(iso))
}

const millisecondFormat = new Intl.NumberFormat('uk-UA', { maximumFractionDigits: 0 })

/** 154.52 -> "155 мс" */
export function formatMilliseconds(ms: number): string {
  return `${millisecondFormat.format(ms)} мс`
}

const percentFormat = new Intl.NumberFormat('uk-UA', {
  style: 'percent',
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
})

const megabyteFormat = new Intl.NumberFormat('uk-UA', { maximumFractionDigits: 1 })

/** 0.8044 -> "80,4 %" */
export function formatPercent(value: number): string {
  return percentFormat.format(value)
}

/** 12_345_678 -> "11,8 МБ" */
export function formatMegabytes(bytes: number): string {
  return `${megabyteFormat.format(bytes / 1024 / 1024)} МБ`
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

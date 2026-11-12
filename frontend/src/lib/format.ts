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

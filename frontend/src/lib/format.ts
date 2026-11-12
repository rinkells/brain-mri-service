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

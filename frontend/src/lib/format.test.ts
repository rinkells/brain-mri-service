import { describe, expect, test } from 'vitest'

import { formatFileSize } from './format'

const KB = 1024
const MB = 1024 * 1024

describe('formatFileSize', () => {
  test.each([
    [14 * KB, '14 КБ'],
    [14_500, '14 КБ'],
    [300, '1 КБ'],
    [1000 * KB, '1000 КБ'],
    [1023 * KB, '1023 КБ'],
  ])('shows %i bytes in kilobytes: %s', (bytes, expected) => {
    expect(formatFileSize(bytes)).toBe(expected)
  })

  test.each([
    [MB, '1 МБ'],
    // 1023,6 KB would round to "1024 КБ": that is already a megabyte.
    [MB - 400, '1 МБ'],
    [Math.round(2.3 * MB), '2,3 МБ'],
    [12_345_678, '11,8 МБ'],
  ])('shows %i bytes in megabytes from 1 MB on: %s', (bytes, expected) => {
    expect(formatFileSize(bytes)).toBe(expected)
  })

  test('never shows a non-empty file as zero', () => {
    expect(formatFileSize(1)).toBe('1 КБ')
  })
})

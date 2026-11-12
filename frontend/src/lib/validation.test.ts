import { describe, expect, test } from 'vitest'

import { makeFile } from '../test/fixtures'
import { MAX_UPLOAD_BYTES } from './config'
import { ACCEPT_ATTRIBUTE, validateImageFile } from './validation'

describe('validateImageFile', () => {
  test.each([
    ['scan.png', 'image/png'],
    ['scan.jpg', 'image/jpeg'],
    ['SCAN.JPEG', 'image/jpeg'],
  ])('accepts %s (%s)', (name, type) => {
    expect(validateImageFile(makeFile(name, type))).toBeNull()
  })

  test.each(['scan.png', 'scan.jpg', 'scan.JPEG'])(
    'falls back to the extension when the type is unknown: %s',
    (name) => {
      expect(validateImageFile(makeFile(name, ''))).toBeNull()
    },
  )

  test.each([
    ['scan.gif', 'image/gif'],
    ['scan.webp', 'image/webp'],
    ['report.pdf', 'application/pdf'],
    ['notes.txt', ''],
    ['scan.png.exe', 'application/x-msdownload'],
  ])('rejects %s (%s)', (name, type) => {
    expect(validateImageFile(makeFile(name, type))).toBe(
      `Файл «${name}» не підходить: потрібен знімок у форматі JPEG або PNG.`,
    )
  })

  test('rejects an empty file', () => {
    expect(validateImageFile(makeFile('scan.png', 'image/png', 0))).toBe(
      'Файл «scan.png» порожній.',
    )
  })

  test('accepts a file of exactly the limit and rejects one byte more', () => {
    expect(validateImageFile(makeFile('a.png', 'image/png', MAX_UPLOAD_BYTES))).toBeNull()

    const message = validateImageFile(makeFile('a.png', 'image/png', MAX_UPLOAD_BYTES + 1))

    expect(message).toMatch(/^Файл завеликий/)
    expect(message).toContain('Максимум — 10 МБ')
  })

  test('uses the given limit', () => {
    expect(validateImageFile(makeFile('a.png', 'image/png', 2 * 1024 * 1024), 1024 * 1024)).toBe(
      'Файл завеликий (2 МБ). Максимум — 1 МБ.',
    )
  })
})

test('accept attribute lists JPEG and PNG only', () => {
  expect(ACCEPT_ATTRIBUTE.split(',')).toEqual(['image/jpeg', 'image/png', '.jpg', '.jpeg', '.png'])
})

import { describe, expect, test } from 'vitest'

import { validateCredentials } from './credentials'

const BAD_USERNAME =
  'Ім’я користувача має містити від 3 до 32 символів: латинські літери, цифри, «_», «.» або «-».'
const BAD_PASSWORD = 'Пароль має містити від 10 до 128 символів.'
const SAME_AS_NAME = 'Пароль не може збігатися з іменем користувача.'
const MISMATCH = 'Паролі не збігаються.'

function register(username: string, password: string, confirm = password) {
  return validateCredentials('register', { username, password, confirm })
}

describe('registration mirrors the backend rules', () => {
  test.each(['abc', 'a'.repeat(32), 'A.b_c-9', 'Alice'])('accepts the name %s', (username) => {
    expect(register(username, 'correct-horse-1')).toEqual({})
  })

  test.each(['ab', 'a'.repeat(33), '', 'with space', 'олена', 'semi;colon', 'tab\t', 'alice\n'])(
    'refuses the name %j',
    (username) => {
      expect(register(username, 'correct-horse-1').username).toBe(BAD_USERNAME)
    },
  )

  test.each([10, 128])('accepts a password of %i characters', (length) => {
    expect(register('alice', 'x'.repeat(length))).toEqual({})
  })

  test.each([0, 9, 129])('refuses a password of %i characters', (length) => {
    expect(register('alice', 'x'.repeat(length)).password).toBe(BAD_PASSWORD)
  })

  test('counts characters, not UTF-16 units, as the backend does', () => {
    // An emoji is one character for Python, but two UTF-16 units.
    expect(register('alice', '😀'.repeat(10))).toEqual({})
    expect(register('alice', '😀'.repeat(65))).toEqual({})
    expect(register('alice', '😀'.repeat(129)).password).toBe(BAD_PASSWORD)
  })

  test('refuses a password equal to the name, whatever the case', () => {
    expect(register('alice_smith', 'alice_smith').password).toBe(SAME_AS_NAME)
    expect(register('Alice_Smith', 'aLICE_sMITH').password).toBe(SAME_AS_NAME)
  })

  test('the confirmation must match', () => {
    expect(register('alice', 'correct-horse-1', 'correct-horse-2')).toEqual({ confirm: MISMATCH })
  })

  test('reports every field at once', () => {
    expect(register('a', 'short', 'other')).toEqual({
      username: BAD_USERNAME,
      password: BAD_PASSWORD,
      confirm: MISMATCH,
    })
  })
})

describe('sign-in only needs both fields, like the backend', () => {
  test('any name and password may be tried', () => {
    expect(validateCredentials('login', { username: 'a', password: 'short' })).toEqual({})
  })

  test('empty fields are refused', () => {
    expect(validateCredentials('login', { username: '', password: '' })).toEqual({
      username: 'Введіть ім’я користувача.',
      password: 'Введіть пароль.',
    })
  })
})

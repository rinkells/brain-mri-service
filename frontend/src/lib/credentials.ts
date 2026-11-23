// The rules of the backend (backend/app/auth.py), checked before anything is sent.

export type CredentialsMode = 'login' | 'register'

export interface CredentialsInput {
  username: string
  password: string
  /** The repeated password of the registration form. */
  confirm?: string
}

export type CredentialsErrors = Partial<Record<keyof CredentialsInput, string>>

export const USERNAME_PATTERN = /^[A-Za-z0-9_.-]{3,32}$/
export const PASSWORD_MIN_LENGTH = 10
export const PASSWORD_MAX_LENGTH = 128

export const MESSAGES = {
  badUsername:
    'Ім’я користувача має містити від 3 до 32 символів: латинські літери, цифри, «_», «.» або «-».',
  badPassword: `Пароль має містити від ${PASSWORD_MIN_LENGTH} до ${PASSWORD_MAX_LENGTH} символів.`,
  passwordIsUsername: 'Пароль не може збігатися з іменем користувача.',
  mismatch: 'Паролі не збігаються.',
  noUsername: 'Введіть ім’я користувача.',
  noPassword: 'Введіть пароль.',
} as const

/** Length in characters (code points), as Python's len() counts it, not UTF-16 units. */
function characters(text: string): number {
  return Array.from(text).length
}

/**
 * Problems with the fields, by field; an empty object when the form may be sent.
 *
 * Registration checks what the backend checks. Sign-in only needs both fields: the
 * backend answers any other name or password with the usual 401, so the form does too.
 */
export function validateCredentials(
  mode: CredentialsMode,
  { username, password, confirm }: CredentialsInput,
): CredentialsErrors {
  const errors: CredentialsErrors = {}

  if (mode === 'login') {
    if (!username) errors.username = MESSAGES.noUsername
    if (!password) errors.password = MESSAGES.noPassword
    return errors
  }

  if (!USERNAME_PATTERN.test(username)) {
    errors.username = MESSAGES.badUsername
  }
  const length = characters(password)
  if (length < PASSWORD_MIN_LENGTH || length > PASSWORD_MAX_LENGTH) {
    errors.password = MESSAGES.badPassword
  } else if (password.toLowerCase() === username.toLowerCase()) {
    errors.password = MESSAGES.passwordIsUsername
  }
  if (password !== confirm) {
    errors.confirm = MESSAGES.mismatch
  }
  return errors
}

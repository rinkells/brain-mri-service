import { useEffect, useId, useRef, useState, type FormEvent, type RefObject } from 'react'

import { login, register } from '../../api/client'
import type { User } from '../../api/types'
import { VIEW_HASH } from '../../hooks/useView'
import {
  validateCredentials,
  type CredentialsErrors,
  type CredentialsInput,
  type CredentialsMode,
} from '../../lib/credentials'
import { ErrorAlert } from '../status/ErrorAlert'

const TEXT = {
  login: {
    title: 'Вхід',
    submit: 'Увійти',
    pending: 'Входимо…',
    switchPrompt: 'Ще немає облікового запису?',
    switchLink: 'Зареєструватися',
    switchHash: VIEW_HASH.register,
  },
  register: {
    title: 'Реєстрація',
    submit: 'Зареєструватися',
    pending: 'Реєструємо…',
    switchPrompt: 'Уже є обліковий запис?',
    switchLink: 'Увійти',
    switchHash: VIEW_HASH.login,
  },
} as const

const HINTS: Partial<Record<keyof CredentialsInput, string>> = {
  username: '3–32 символи: латинські літери, цифри, «_», «.» або «-».',
  password: '10–128 символів, не таке саме, як ім’я користувача.',
}

const FIELD_ORDER: (keyof CredentialsInput)[] = ['username', 'password', 'confirm']

const INPUT_CLASS =
  'block w-full rounded-lg border bg-white px-3 py-2 text-slate-900 focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:outline-none dark:bg-slate-900 dark:text-slate-100'

interface FieldProps {
  label: string
  value: string
  onChange: (value: string) => void
  type: 'text' | 'password'
  autoComplete: string
  error?: string
  hint?: string
  inputRef: RefObject<HTMLInputElement | null>
}

function Field({ label, value, onChange, type, autoComplete, error, hint, inputRef }: FieldProps) {
  const id = useId()
  const errorId = `${id}-error`
  const hintId = `${id}-hint`
  // While there is an error, it alone describes the field; otherwise the hint does.
  const describedBy = error ? errorId : hint ? hintId : undefined

  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-sm font-medium">
        {label}
      </label>
      <input
        ref={inputRef}
        id={id}
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete={autoComplete}
        autoCapitalize="none"
        autoCorrect="off"
        spellCheck={false}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        className={`${INPUT_CLASS} ${
          error ? 'border-red-500 dark:border-red-500' : 'border-slate-300 dark:border-slate-700'
        }`}
      />
      {error ? (
        <p id={errorId} className="text-sm text-red-700 dark:text-red-300">
          {error}
        </p>
      ) : (
        hint && (
          <p id={hintId} className="text-xs text-slate-500 dark:text-slate-400">
            {hint}
          </p>
        )
      )}
    </div>
  )
}

interface AuthFormProps {
  mode: CredentialsMode
  onSuccess: (user: User) => void
}

/**
 * Sign-in or registration. The passwords live only in the state of this form and are
 * emptied after every request, whatever its outcome.
 */
export function AuthForm({ mode, onSuccess }: AuthFormProps) {
  const text = TEXT[mode]
  const headingId = useId()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [errors, setErrors] = useState<CredentialsErrors>({})
  const [serverError, setServerError] = useState<unknown>(null)
  const [pending, setPending] = useState(false)
  const usernameRef = useRef<HTMLInputElement>(null)
  const passwordRef = useRef<HTMLInputElement>(null)
  const confirmRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    usernameRef.current?.focus()
  }, [])

  function focusField(name: keyof CredentialsInput) {
    const ref = { username: usernameRef, password: passwordRef, confirm: confirmRef }[name]
    ref.current?.focus()
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending) {
      return
    }
    const input = mode === 'register' ? { username, password, confirm } : { username, password }
    const problems = validateCredentials(mode, input)
    setErrors(problems)
    setServerError(null)
    const firstInvalid = FIELD_ORDER.find((name) => problems[name])
    if (firstInvalid) {
      focusField(firstInvalid)
      return
    }

    setPending(true)
    try {
      const user = await (mode === 'login' ? login : register)(username, password)
      onSuccess(user)
    } catch (error) {
      setServerError(error)
      passwordRef.current?.focus()
    } finally {
      setPassword('')
      setConfirm('')
      setPending(false)
    }
  }

  const passwordType = showPassword ? 'text' : 'password'

  return (
    <form
      onSubmit={handleSubmit}
      aria-labelledby={headingId}
      noValidate
      className="mx-auto max-w-md space-y-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm dark:border-slate-800 dark:bg-slate-900"
    >
      <h2 id={headingId} className="text-lg font-semibold">
        {text.title}
      </h2>

      <Field
        label="Ім’я користувача"
        value={username}
        onChange={setUsername}
        type="text"
        autoComplete="username"
        error={errors.username}
        hint={mode === 'register' ? HINTS.username : undefined}
        inputRef={usernameRef}
      />
      <Field
        label="Пароль"
        value={password}
        onChange={setPassword}
        type={passwordType}
        autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
        error={errors.password}
        hint={mode === 'register' ? HINTS.password : undefined}
        inputRef={passwordRef}
      />
      {mode === 'register' && (
        <Field
          label="Підтвердження пароля"
          value={confirm}
          onChange={setConfirm}
          type={passwordType}
          autoComplete="new-password"
          error={errors.confirm}
          inputRef={confirmRef}
        />
      )}

      <label className="inline-flex cursor-pointer items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={showPassword}
          onChange={(event) => setShowPassword(event.target.checked)}
          className="size-4 rounded border-slate-300 text-sky-600 focus-visible:ring-2 focus-visible:ring-sky-500 dark:border-slate-600"
        />
        Показати пароль
      </label>

      {/* Announced as soon as it appears. */}
      <div aria-live="assertive">{serverError != null && <ErrorAlert error={serverError} />}</div>

      <button
        type="submit"
        disabled={pending}
        aria-busy={pending}
        className="w-full rounded-lg bg-sky-600 px-5 py-2.5 font-medium text-white shadow-sm transition-colors hover:bg-sky-700 focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-offset-2 focus-visible:outline-none disabled:cursor-not-allowed disabled:bg-slate-400 dark:focus-visible:ring-offset-slate-900 dark:disabled:bg-slate-700"
      >
        {pending ? text.pending : text.submit}
      </button>

      <p className="text-center text-sm text-slate-600 dark:text-slate-300">
        {text.switchPrompt}{' '}
        <a
          href={text.switchHash}
          className="font-medium text-sky-700 underline-offset-2 hover:underline dark:text-sky-400"
        >
          {text.switchLink}
        </a>
      </p>
    </form>
  )
}

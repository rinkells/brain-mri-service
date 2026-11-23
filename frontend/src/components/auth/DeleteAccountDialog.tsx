import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'

import { deleteAccount } from '../../api/client'
import { PASSWORD_MAX_LENGTH } from '../../lib/credentials'
import { ErrorAlert } from '../status/ErrorAlert'

interface DeleteAccountDialogProps {
  username: string
  onCancel: () => void
  onDeleted: () => void
}

const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), a[href]'

/**
 * Asks for the password before DELETE /auth/me. A wrong password (403) keeps the user
 * signed in and says so; 204 ends here and the caller signs out. The password is
 * emptied after every attempt.
 */
export function DeleteAccountDialog({ username, onCancel, onDeleted }: DeleteAccountDialogProps) {
  const titleId = useId()
  const descriptionId = useId()
  const passwordId = useId()
  const [password, setPassword] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [pending, setPending] = useState(false)
  const dialogRef = useRef<HTMLDivElement>(null)
  const passwordRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    passwordRef.current?.focus()
  }, [])

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending || !password) {
      passwordRef.current?.focus()
      return
    }
    setPending(true)
    setError(null)
    try {
      await deleteAccount(password)
      onDeleted()
    } catch (reason) {
      setError(reason)
      setPending(false)
      passwordRef.current?.focus()
    } finally {
      setPassword('')
    }
  }

  // Escape cancels; Tab stays inside the dialog.
  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape' && !pending) {
      event.stopPropagation()
      onCancel()
      return
    }
    if (event.key !== 'Tab' || !dialogRef.current) {
      return
    }
    const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE))
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last?.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first?.focus()
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 p-4">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        onKeyDown={handleKeyDown}
        className="w-full max-w-md space-y-4 rounded-2xl border border-slate-200 bg-white p-6 text-slate-900 shadow-xl dark:border-slate-800 dark:bg-slate-900 dark:text-slate-100"
      >
        <h2 id={titleId} className="text-lg font-semibold">
          Видалити обліковий запис?
        </h2>
        <p id={descriptionId} className="text-sm text-slate-700 dark:text-slate-300">
          Буде назавжди видалено обліковий запис {username}, усі сесії (вихід на всіх пристроях) і
          уся історія прогнозів. Цю дію не можна скасувати.
        </p>
        <form onSubmit={handleSubmit} noValidate className="space-y-4">
          <div className="space-y-1">
            <label htmlFor={passwordId} className="block text-sm font-medium">
              Пароль
            </label>
            <input
              ref={passwordRef}
              id={passwordId}
              type="password"
              autoComplete="current-password"
              maxLength={PASSWORD_MAX_LENGTH}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              className="block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:outline-none dark:border-slate-700 dark:bg-slate-900"
            />
          </div>
          <div aria-live="assertive">{error != null && <ErrorAlert error={error} />}</div>
          <div className="flex flex-wrap justify-end gap-3">
            <button
              type="button"
              onClick={onCancel}
              disabled={pending}
              className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:outline-none dark:border-slate-700 dark:hover:bg-slate-800"
            >
              Скасувати
            </button>
            <button
              type="submit"
              disabled={pending}
              aria-busy={pending}
              className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 focus-visible:ring-2 focus-visible:ring-red-500 focus-visible:ring-offset-2 focus-visible:outline-none disabled:cursor-not-allowed disabled:bg-slate-400 dark:focus-visible:ring-offset-slate-900"
            >
              {pending ? 'Видаляємо…' : 'Видалити назавжди'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

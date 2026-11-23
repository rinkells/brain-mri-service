import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'

import { useAuth } from '../../hooks/useAuth'
import { VIEW_HASH } from '../../hooks/useView'
import { DeleteAccountDialog } from './DeleteAccountDialog'

const LINK_CLASS =
  'rounded-full px-3 py-1.5 text-sm font-medium focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:outline-none'

/** The header's corner: sign-in links for a visitor, the user's name with a menu otherwise. */
export function AccountArea() {
  const { auth } = useAuth()

  if (auth.status === 'loading') {
    return null
  }
  if (auth.status === 'anonymous') {
    return (
      <div className="flex items-center gap-1">
        <a
          href={VIEW_HASH.login}
          className={`${LINK_CLASS} text-slate-700 hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-slate-800`}
        >
          Увійти
        </a>
        <a
          href={VIEW_HASH.register}
          className={`${LINK_CLASS} bg-sky-600 text-white hover:bg-sky-700`}
        >
          Зареєструватися
        </a>
      </div>
    )
  }
  return <AccountMenu username={auth.user.username} />
}

function AccountMenu({ username }: { username: string }) {
  const { signOut, accountDeleted } = useAuth()
  const menuId = useId()
  const [open, setOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const wrapperRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const firstItemRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) {
      return
    }
    firstItemRef.current?.focus()
    function closeOutside(event: MouseEvent) {
      if (!wrapperRef.current?.contains(event.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', closeOutside)
    return () => document.removeEventListener('mousedown', closeOutside)
  }, [open])

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape' && open) {
      setOpen(false)
      buttonRef.current?.focus()
    }
  }

  function cancelDeletion() {
    setDeleting(false)
    buttonRef.current?.focus()
  }

  const itemClass =
    'block w-full px-4 py-2 text-left text-sm hover:bg-slate-100 focus-visible:bg-slate-100 focus-visible:outline-none dark:hover:bg-slate-800 dark:focus-visible:bg-slate-800'

  return (
    <div ref={wrapperRef} className="relative" onKeyDown={handleKeyDown}>
      <button
        ref={buttonRef}
        type="button"
        aria-expanded={open}
        aria-controls={menuId}
        onClick={() => setOpen((value) => !value)}
        className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:outline-none dark:border-slate-700 dark:bg-slate-900 dark:hover:bg-slate-800"
      >
        <span className="sr-only">Обліковий запис </span>
        <span className="max-w-40 truncate">{username}</span>
        <svg aria-hidden="true" viewBox="0 0 20 20" className="size-4" fill="currentColor">
          <path d="M5.3 7.3a1 1 0 0 1 1.4 0L10 10.6l3.3-3.3a1 1 0 1 1 1.4 1.4l-4 4a1 1 0 0 1-1.4 0l-4-4a1 1 0 0 1 0-1.4z" />
        </svg>
      </button>
      {open && (
        <ul
          id={menuId}
          className="absolute right-0 z-40 mt-2 w-48 overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-lg dark:border-slate-700 dark:bg-slate-900"
        >
          <li>
            <button
              ref={firstItemRef}
              type="button"
              className={itemClass}
              onClick={() => {
                setOpen(false)
                void signOut()
              }}
            >
              Вийти
            </button>
          </li>
          <li>
            <button
              type="button"
              className={`${itemClass} text-red-700 dark:text-red-300`}
              onClick={() => {
                setOpen(false)
                setDeleting(true)
              }}
            >
              Видалити акаунт
            </button>
          </li>
        </ul>
      )}
      {deleting && (
        <DeleteAccountDialog
          username={username}
          onCancel={cancelDeletion}
          onDeleted={accountDeleted}
        />
      )}
    </div>
  )
}

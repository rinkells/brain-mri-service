import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { getMe, logout, onSessionExpired } from '../../api/client'
import { describeError } from '../../api/errors'
import type { User } from '../../api/types'
import { AuthContext, type AuthState, type Notice } from '../../hooks/useAuth'
import { VIEW_HASH } from '../../hooks/useView'

const SESSION_ENDED: Notice = {
  tone: 'alert',
  title: 'Сеанс завершився',
  description: 'Увійдіть знову.',
  offerSignIn: true,
}
const ACCOUNT_DELETED: Notice = {
  tone: 'info',
  title: 'Обліковий запис видалено',
  description: 'Разом з ним видалено всі його сесії й усю історію прогнозів.',
}

/**
 * Asks GET /auth/me once on load (401: anonymous) and keeps the answer in memory only:
 * nothing goes to localStorage or sessionStorage. A 401 from any route that needs a
 * session (reported by the API client) signs the user out here.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [auth, setAuth] = useState<AuthState>({ status: 'loading' })
  const [notice, setNotice] = useState<Notice | null>(null)
  const [resetCount, setResetCount] = useState(0)
  const authRef = useRef(auth)

  useEffect(() => {
    authRef.current = auth
  }, [auth])

  useEffect(() => {
    const controller = new AbortController()
    getMe(controller.signal).then(
      (user) => setAuth(user ? { status: 'signed-in', user } : { status: 'anonymous' }),
      () => {
        // Unreachable or without a database: nobody can be signed in.
        if (!controller.signal.aborted) {
          setAuth({ status: 'anonymous' })
        }
      },
    )
    return () => controller.abort()
  }, [])

  useEffect(
    () =>
      onSessionExpired((source) => {
        if (authRef.current.status !== 'signed-in') {
          return
        }
        authRef.current = { status: 'anonymous' }
        setAuth({ status: 'anonymous' })
        // /predict explains it next to the scan; anything else gets the notice.
        if (source !== 'predict') {
          setNotice(SESSION_ENDED)
        }
      }),
    [],
  )

  const forget = useCallback((next: Notice | null) => {
    authRef.current = { status: 'anonymous' }
    setAuth({ status: 'anonymous' })
    setResetCount((count) => count + 1)
    setNotice(next)
    window.location.hash = VIEW_HASH.analysis
  }, [])

  const signedIn = useCallback((user: User) => {
    authRef.current = { status: 'signed-in', user }
    setAuth({ status: 'signed-in', user })
    setNotice(null)
  }, [])

  const signOut = useCallback(async () => {
    try {
      await logout()
    } catch (error) {
      // The session may still be valid on the server: stay signed in and say why.
      setNotice({ tone: 'alert', ...describeError(error) })
      return
    }
    forget(null)
  }, [forget])

  const accountDeleted = useCallback(() => forget(ACCOUNT_DELETED), [forget])
  const dismissNotice = useCallback(() => setNotice(null), [])

  const value = useMemo(
    () => ({ auth, notice, resetCount, signedIn, signOut, accountDeleted, dismissNotice }),
    [auth, notice, resetCount, signedIn, signOut, accountDeleted, dismissNotice],
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

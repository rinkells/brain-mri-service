import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { getMe, logout, onSessionExpired } from '../../api/client'
import { ApiError, describeError } from '../../api/errors'
import type { User } from '../../api/types'
import { AuthContext, type AuthState, type Notice } from '../../hooks/useAuth'
import { VIEW_HASH } from '../../hooks/useView'

const SESSION_ENDED: Notice = {
  tone: 'alert',
  title: 'Сеанс завершився',
  description: 'Увійдіть знову.',
  offerSignIn: true,
}
/** Waits before asking GET /auth/me again after a failure without Retry-After: 5 s, then
 * twice as long each time, up to a minute. */
const FIRST_RETRY_S = 5
const LAST_RETRY_S = 60

function retryDelayMs(error: unknown, failures: number): number {
  const seconds =
    error instanceof ApiError && error.retryAfter !== undefined
      ? Math.max(1, error.retryAfter)
      : Math.min(FIRST_RETRY_S * 2 ** (failures - 1), LAST_RETRY_S)
  return seconds * 1000
}

/** Not yet known who it is: the answer of GET /auth/me may still change that. */
function undecided(state: AuthState): boolean {
  return state.status === 'loading' || state.status === 'unknown'
}

const ACCOUNT_DELETED: Notice = {
  tone: 'info',
  title: 'Обліковий запис видалено',
  description: 'Разом з ним видалено всі його сесії й усю історію прогнозів.',
}

/**
 * Asks GET /auth/me on load (401: anonymous) and keeps the answer in memory only: nothing
 * goes to localStorage or sessionStorage. A 503 with database_not_configured means there
 * are no accounts: anonymous, for good. Any other failure (429, 503, no connection) says
 * nothing about the session, so the state becomes 'unknown' and the question is asked
 * again later. A 401 from any route that needs a session (reported by the API
 * client) signs the user out here.
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
    let timer: number | undefined
    let failures = 0

    function settle(next: AuthState) {
      // A sign-in or a 401 elsewhere may have decided it in the meantime.
      if (undecided(authRef.current)) {
        authRef.current = next
        setAuth(next)
      }
    }

    function ask() {
      getMe(controller.signal).then(
        (user) => settle(user ? { status: 'signed-in', user } : { status: 'anonymous' }),
        (error: unknown) => {
          if (controller.signal.aborted || !undecided(authRef.current)) {
            return
          }
          if (error instanceof ApiError && error.code === 'database_not_configured') {
            // No accounts at all: nothing will change by asking again.
            settle({ status: 'anonymous' })
            return
          }
          failures += 1
          settle({ status: 'unknown' })
          timer = window.setTimeout(ask, retryDelayMs(error, failures))
        },
      )
    }

    ask()
    return () => {
      controller.abort()
      window.clearTimeout(timer)
    }
  }, [])

  useEffect(
    () =>
      onSessionExpired((source) => {
        const { status } = authRef.current
        if (status === 'unknown') {
          // Now it is known: no valid session. Nobody was shown as signed in, so no notice.
          authRef.current = { status: 'anonymous' }
          setAuth({ status: 'anonymous' })
          return
        }
        if (status !== 'signed-in') {
          return
        }
        authRef.current = { status: 'anonymous' }
        setAuth({ status: 'anonymous' })
        // /predict explains it next to the scan, which must stay. Anything else gets the
        // notice, and the analysis page forgets the user's last scan and result, as at
        // sign-out: the next person at this browser must not see them.
        if (source !== 'predict') {
          setNotice(SESSION_ENDED)
          setResetCount((count) => count + 1)
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

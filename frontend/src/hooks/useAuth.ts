import { createContext, useContext } from 'react'

import type { User } from '../api/types'

/** Who uses the interface. The session itself is an HttpOnly cookie the page never sees. */
export type AuthState =
  | { status: 'loading' } // GET /auth/me has not answered yet
  // GET /auth/me failed for another reason than 401 (429, 503, no connection): nobody is
  // shown as a guest or as signed in, and it is asked again a little later.
  | { status: 'unknown' }
  | { status: 'anonymous' }
  | { status: 'signed-in'; user: User }

/** A message above the page: the session ended, the account was deleted, and the like. */
export interface Notice {
  tone: 'alert' | 'info'
  title: string
  description: string
  /** Show a link to the sign-in page. */
  offerSignIn?: boolean
}

export interface AuthContextValue {
  auth: AuthState
  notice: Notice | null
  /** Grows on every sign-out, account deletion and session ended (401 outside /predict):
   * pages keyed by it forget their data. */
  resetCount: number
  /** After a successful sign-in or registration. */
  signedIn: (user: User) => void
  /** POST /auth/logout, then forget the user and go to the main page. */
  signOut: () => Promise<void>
  /** After DELETE /auth/me answered 204. */
  accountDeleted: () => void
  dismissNotice: () => void
}

function noop() {}

export const AuthContext = createContext<AuthContextValue>({
  auth: { status: 'anonymous' },
  notice: null,
  resetCount: 0,
  signedIn: noop,
  signOut: async () => {},
  accountDeleted: noop,
  dismissNotice: noop,
})

export function useAuth(): AuthContextValue {
  return useContext(AuthContext)
}

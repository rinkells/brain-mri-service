import { useSyncExternalStore } from 'react'

export type View = 'analysis' | 'history' | 'login' | 'register'
/** The pages of the service itself, as opposed to signing in. */
export type ContentView = Extract<View, 'analysis' | 'history'>

/** The page is chosen by the URL hash, so reload and the back button keep it. */
export const VIEW_HASH: Record<View, string> = {
  analysis: '#/',
  history: '#/history',
  login: '#/login',
  register: '#/register',
}

const VIEW_BY_HASH = new Map(Object.entries(VIEW_HASH).map(([view, hash]) => [hash, view as View]))

function subscribe(onChange: () => void) {
  window.addEventListener('hashchange', onChange)
  return () => window.removeEventListener('hashchange', onChange)
}

export function useView(): View {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash)
  // Anything else, an empty hash included, is the main page.
  return VIEW_BY_HASH.get(hash) ?? 'analysis'
}

export function isContentView(view: View): view is ContentView {
  return view === 'analysis' || view === 'history'
}

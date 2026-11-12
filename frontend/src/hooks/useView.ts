import { useSyncExternalStore } from 'react'

export type View = 'analysis' | 'history'

/** The page is chosen by the URL hash, so reload and the back button keep it. */
export const VIEW_HASH: Record<View, string> = {
  analysis: '#/',
  history: '#/history',
}

function subscribe(onChange: () => void) {
  window.addEventListener('hashchange', onChange)
  return () => window.removeEventListener('hashchange', onChange)
}

export function useView(): View {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash)
  return hash === VIEW_HASH.history ? 'history' : 'analysis'
}

import { useEffect, useState } from 'react'

import { getHealth } from '../api/client'
import { HEALTH_POLL_MS } from '../lib/config'

export type HealthState =
  { status: 'checking' } | { status: 'online'; device: string } | { status: 'offline' }

function sameHealth(a: HealthState, b: HealthState): boolean {
  if (a.status === 'online' && b.status === 'online') {
    return a.device === b.device
  }
  return a.status === b.status
}

/**
 * Poll GET /health and report whether the backend is reachable. The state changes only
 * when the answer does, so a poll that finds everything as before re-renders nothing.
 */
export function useHealth(intervalMs = HEALTH_POLL_MS): HealthState {
  const [state, setState] = useState<HealthState>({ status: 'checking' })

  useEffect(() => {
    let controller: AbortController | undefined

    function report(next: HealthState) {
      setState((previous) => (sameHealth(previous, next) ? previous : next))
    }

    async function check() {
      controller?.abort()
      const current = new AbortController()
      controller = current
      try {
        const health = await getHealth(current.signal)
        report({ status: 'online', device: health.device })
      } catch {
        if (!current.signal.aborted) {
          report({ status: 'offline' })
        }
      }
    }

    void check()
    const timer = window.setInterval(() => void check(), intervalMs)
    return () => {
      window.clearInterval(timer)
      controller?.abort()
    }
  }, [intervalMs])

  return state
}

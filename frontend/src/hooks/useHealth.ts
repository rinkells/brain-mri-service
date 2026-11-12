import { useEffect, useState } from 'react'

import { getHealth } from '../api/client'
import { HEALTH_POLL_MS } from '../lib/config'

export type HealthState =
  { status: 'checking' } | { status: 'online'; device: string } | { status: 'offline' }

/** Poll GET /health and report whether the backend is reachable. */
export function useHealth(intervalMs = HEALTH_POLL_MS): HealthState {
  const [state, setState] = useState<HealthState>({ status: 'checking' })

  useEffect(() => {
    let controller: AbortController | undefined

    async function check() {
      controller?.abort()
      const current = new AbortController()
      controller = current
      try {
        const health = await getHealth(current.signal)
        setState({ status: 'online', device: health.device })
      } catch {
        if (!current.signal.aborted) {
          setState({ status: 'offline' })
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

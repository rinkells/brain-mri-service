import { useEffect, useState } from 'react'

import { getStats, listPredictions, type HistoryQuery } from '../api/client'
import type { PredictionPage, StatsResponse } from '../api/types'

interface Loaded<T> {
  /** Which request this result belongs to. */
  key: string
  data?: T
  error?: unknown
}

interface Resource<T> {
  /** True until the response for the current parameters arrives. */
  loading: boolean
  /** The latest data; kept while the next page loads to avoid flicker. */
  data?: T
  error?: unknown
}

function toResource<T>(loaded: Loaded<T> | undefined, key: string): Resource<T> {
  const current = loaded?.key === key
  return {
    loading: !current,
    data: loaded?.data,
    error: current ? loaded?.error : undefined,
  }
}

/** One page of GET /predictions; refetched when the query or reloadToken changes. */
export function useHistoryPage(query: HistoryQuery, reloadToken: number): Resource<PredictionPage> {
  const { limit, offset, predictedClass } = query
  const key = [limit, offset, predictedClass ?? '', reloadToken].join('|')
  const [loaded, setLoaded] = useState<Loaded<PredictionPage>>()

  useEffect(() => {
    const controller = new AbortController()
    listPredictions({ limit, offset, predictedClass }, controller.signal).then(
      (data) => setLoaded({ key, data }),
      (error: unknown) => {
        if (!controller.signal.aborted) {
          setLoaded({ key, error })
        }
      },
    )
    return () => controller.abort()
  }, [key, limit, offset, predictedClass])

  return toResource(loaded, key)
}

/** GET /stats; refetched when reloadToken changes. */
export function useStats(reloadToken: number): Resource<StatsResponse> {
  const key = String(reloadToken)
  const [loaded, setLoaded] = useState<Loaded<StatsResponse>>()

  useEffect(() => {
    const controller = new AbortController()
    getStats(controller.signal).then(
      (data) => setLoaded({ key, data }),
      (error: unknown) => {
        if (!controller.signal.aborted) {
          setLoaded({ key, error })
        }
      },
    )
    return () => controller.abort()
  }, [key])

  return toResource(loaded, key)
}

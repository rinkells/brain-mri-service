import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, test } from 'vitest'

import { callsTo, healthyResponse, mockApi } from '../test/fixtures'
import { useHealth } from './useHealth'

const POLL_MS = 20

describe('useHealth', () => {
  test('keeps the same state while the answer stays the same', async () => {
    const fetchMock = mockApi()
    const { result } = renderHook(() => useHealth(POLL_MS))
    await waitFor(() => expect(result.current).toEqual({ status: 'online', device: 'cpu' }))
    const online = result.current
    const polls = callsTo(fetchMock, '/health').length

    await waitFor(() => expect(callsTo(fetchMock, '/health').length).toBeGreaterThan(polls + 2))

    // The same object: React has no new state to re-render the app with.
    expect(result.current).toBe(online)
  })

  test('still reports a change', async () => {
    let up = true
    mockApi({ health: () => (up ? healthyResponse() : Promise.reject(new TypeError('down'))) })
    const { result } = renderHook(() => useHealth(POLL_MS))
    await waitFor(() => expect(result.current.status).toBe('online'))

    up = false

    await waitFor(() => expect(result.current).toEqual({ status: 'offline' }))
  })
})

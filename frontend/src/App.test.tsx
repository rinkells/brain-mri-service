import { render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import App from './App'

afterEach(() => {
  vi.unstubAllGlobals()
})

test('renders the page title', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => Response.json({ status: 'ok', device: 'cpu', classes: [] })),
  )

  render(<App />)

  expect(screen.getByRole('heading', { name: 'Класифікація МРТ мозку' })).toBeInTheDocument()
  expect(await screen.findByText('Сервіс працює')).toBeInTheDocument()
})

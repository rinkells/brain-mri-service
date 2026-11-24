import { act, render, screen } from '@testing-library/react'
import { StrictMode, type ReactElement } from 'react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

import { makeFile } from '../test/fixtures'
import { useImageSelection } from './useImageSelection'

type Api = ReturnType<typeof useImageSelection>

/** Shows the preview like the upload zone does, and hands the hook out to the test. */
function Harness({ expose }: { expose: (api: Api) => void }) {
  const api = useImageSelection()
  expose(api)
  return api.selection ? <img src={api.selection.previewUrl} alt="preview" /> : null
}

function setup(options: { strict?: boolean } = {}) {
  const ref: { current: Api | null } = { current: null }
  const expose = (api: Api) => {
    ref.current = api
  }
  const element = (key: number) => <Harness key={key} expose={expose} />
  const wrap = (node: ReactElement) => (options.strict ? <StrictMode>{node}</StrictMode> : node)
  const view = render(wrap(element(0)))
  return {
    api: () => ref.current!,
    unmount: view.unmount,
    remount: () => view.rerender(wrap(element(1))),
  }
}

let created = 0

beforeEach(() => {
  created = 0
  // A distinct URL per file, so the test can tell which one was released.
  vi.mocked(URL.createObjectURL).mockImplementation(() => `blob:preview-${++created}`)
})

afterEach(() => {
  vi.mocked(URL.createObjectURL).mockImplementation(() => 'blob:preview')
})

describe('useImageSelection', () => {
  test('releases the preview URL when the component unmounts', () => {
    const harness = setup()
    act(() => harness.api().select(makeFile()))
    const url = harness.api().selection!.previewUrl

    harness.unmount()

    expect(URL.revokeObjectURL).toHaveBeenCalledWith(url)
  })

  test('releases the previous preview URL when another file is chosen', () => {
    const harness = setup()
    act(() => harness.api().select(makeFile()))
    const first = harness.api().selection!.previewUrl

    act(() => harness.api().select(makeFile()))

    expect(URL.revokeObjectURL).toHaveBeenCalledWith(first)
    expect(URL.revokeObjectURL).not.toHaveBeenCalledWith(harness.api().selection!.previewUrl)
    expect(screen.getByRole('img', { name: 'preview' })).toHaveAttribute(
      'src',
      harness.api().selection!.previewUrl,
    )
  })

  test('releases the preview URL when a new key resets the component', () => {
    const harness = setup()
    act(() => harness.api().select(makeFile()))
    const url = harness.api().selection!.previewUrl

    harness.remount()

    expect(URL.revokeObjectURL).toHaveBeenCalledWith(url)
    expect(harness.api().selection).toBeNull()
  })

  test('releases the preview URL when the selection is cleared', () => {
    const harness = setup()
    act(() => harness.api().select(makeFile()))
    const url = harness.api().selection!.previewUrl

    act(() => harness.api().clear())

    expect(URL.revokeObjectURL).toHaveBeenCalledWith(url)
  })

  test('keeps the shown preview URL alive under StrictMode', () => {
    const harness = setup({ strict: true })
    act(() => harness.api().select(makeFile()))
    const url = harness.api().selection!.previewUrl

    expect(URL.revokeObjectURL).not.toHaveBeenCalledWith(url)
    expect(screen.getByRole('img', { name: 'preview' })).toHaveAttribute('src', url)

    harness.unmount()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith(url)
  })
})

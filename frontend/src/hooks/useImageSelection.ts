import { useEffect, useState } from 'react'

export interface ImageSelection {
  file: File
  /** Object URL for showing the file; revoked when the selection changes or the page unmounts. */
  previewUrl: string
}

/** The file chosen by the user, with a preview URL that is cleaned up on change. */
export function useImageSelection() {
  const [selection, setSelection] = useState<ImageSelection | null>(null)
  const previewUrl = selection?.previewUrl

  // The effect owns each URL: its cleanup releases it once another one is shown, the
  // selection is cleared or the component unmounts (e.g. remounted under a new key at
  // sign-out). The hook always starts empty, so the extra setup and cleanup StrictMode
  // runs at mount has no URL to release; a URL is never released while it is shown.
  useEffect(() => {
    if (previewUrl === undefined) {
      return
    }
    return () => URL.revokeObjectURL(previewUrl)
  }, [previewUrl])

  function select(file: File) {
    setSelection({ file, previewUrl: URL.createObjectURL(file) })
  }

  function clear() {
    setSelection(null)
  }

  return { selection, select, clear }
}

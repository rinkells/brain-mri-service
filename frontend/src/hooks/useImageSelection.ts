import { useState } from 'react'

export interface ImageSelection {
  file: File
  /** Object URL for showing the file; revoked when the selection changes. */
  previewUrl: string
}

/** The file chosen by the user, with a preview URL that is cleaned up on change. */
export function useImageSelection() {
  const [selection, setSelection] = useState<ImageSelection | null>(null)

  function select(file: File) {
    if (selection) {
      URL.revokeObjectURL(selection.previewUrl)
    }
    setSelection({ file, previewUrl: URL.createObjectURL(file) })
  }

  function clear() {
    if (selection) {
      URL.revokeObjectURL(selection.previewUrl)
    }
    setSelection(null)
  }

  return { selection, select, clear }
}

import { useId, useState, type ChangeEvent, type DragEvent } from 'react'

import type { ImageSelection } from '../../hooks/useImageSelection'
import { MAX_UPLOAD_MB } from '../../lib/config'
import { formatMegabytes } from '../../lib/format'
import { ACCEPT_ATTRIBUTE } from '../../lib/validation'

interface UploadZoneProps {
  selection: ImageSelection | null
  error: string | null
  onFile: (file: File) => void
  onClear: () => void
}

export function UploadZone({ selection, error, onFile, onClear }: UploadZoneProps) {
  const inputId = useId()
  const errorId = useId()
  const [dragging, setDragging] = useState(false)

  function handleChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    // Reset, so choosing the same file again still fires a change event.
    event.target.value = ''
    if (file) {
      onFile(file)
    }
  }

  function handleDragOver(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'copy'
    setDragging(true)
  }

  function handleDragLeave(event: DragEvent<HTMLDivElement>) {
    // Moving over a child element also fires dragleave on the zone.
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
      setDragging(false)
    }
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setDragging(false)
    const file = event.dataTransfer.files[0]
    if (file) {
      onFile(file)
    }
  }

  return (
    <div className="space-y-3">
      <div
        data-testid="drop-zone"
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={[
          'flex flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed p-6 text-center transition-colors',
          dragging
            ? 'border-sky-500 bg-sky-50 dark:bg-sky-950/40'
            : 'border-slate-300 bg-white dark:border-slate-700 dark:bg-slate-900',
        ].join(' ')}
      >
        {selection ? (
          <img
            src={selection.previewUrl}
            alt="Прев’ю вибраного знімка"
            className="max-h-64 rounded-lg bg-black object-contain"
          />
        ) : (
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            className="size-12 text-slate-400"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          >
            <path d="M12 16V4m0 0-4 4m4-4 4 4M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" />
          </svg>
        )}

        <p className="text-sm text-slate-600 dark:text-slate-300">
          {selection
            ? 'Щоб замінити знімок, перетягніть інший або '
            : 'Перетягніть знімок сюди або '}
          <label
            htmlFor={inputId}
            className="cursor-pointer font-medium text-sky-700 underline underline-offset-2 hover:text-sky-900 dark:text-sky-400 dark:hover:text-sky-300"
          >
            оберіть файл
          </label>
        </p>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          JPEG або PNG, до {MAX_UPLOAD_MB} МБ
        </p>

        <input
          id={inputId}
          type="file"
          accept={ACCEPT_ATTRIBUTE}
          aria-label="Файл знімка МРТ"
          aria-describedby={error ? errorId : undefined}
          className="sr-only"
          onChange={handleChange}
        />
      </div>

      {selection && (
        <div className="flex items-center justify-between gap-3 rounded-lg bg-slate-100 px-3 py-2 text-sm dark:bg-slate-800">
          <span className="truncate" title={selection.file.name}>
            {selection.file.name}
            <span className="text-slate-500 dark:text-slate-400">
              {' · '}
              {formatMegabytes(selection.file.size)}
            </span>
          </span>
          <button
            type="button"
            onClick={onClear}
            className="shrink-0 rounded-md px-2 py-1 font-medium text-slate-700 hover:bg-slate-200 dark:text-slate-200 dark:hover:bg-slate-700"
          >
            Прибрати
          </button>
        </div>
      )}

      {error && (
        <p id={errorId} role="alert" className="text-sm text-red-700 dark:text-red-400">
          {error}
        </p>
      )}
    </div>
  )
}

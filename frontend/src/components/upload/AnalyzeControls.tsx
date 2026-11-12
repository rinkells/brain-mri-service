interface AnalyzeControlsProps {
  gradcam: boolean
  onGradcamChange: (value: boolean) => void
  canSubmit: boolean
  loading: boolean
}

/** Heat map switch and the submit button; rendered inside the upload form. */
export function AnalyzeControls({
  gradcam,
  onGradcamChange,
  canSubmit,
  loading,
}: AnalyzeControlsProps) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-4">
      <label className="inline-flex cursor-pointer items-center gap-3 text-sm">
        <input
          type="checkbox"
          role="switch"
          checked={gradcam}
          onChange={(event) => onGradcamChange(event.target.checked)}
          className="peer sr-only"
        />
        <span
          aria-hidden="true"
          className="relative h-6 w-11 rounded-full bg-slate-300 transition-colors peer-checked:bg-sky-600 peer-focus-visible:ring-2 peer-focus-visible:ring-sky-500 peer-focus-visible:ring-offset-2 after:absolute after:top-0.5 after:left-0.5 after:size-5 after:rounded-full after:bg-white after:shadow after:transition-transform peer-checked:after:translate-x-5 dark:bg-slate-700 dark:peer-focus-visible:ring-offset-slate-900"
        />
        Показати теплову карту
      </label>

      <button
        type="submit"
        disabled={!canSubmit || loading}
        aria-busy={loading}
        className="inline-flex items-center gap-2 rounded-lg bg-sky-600 px-5 py-2.5 font-medium text-white shadow-sm transition-colors hover:bg-sky-700 focus-visible:ring-2 focus-visible:ring-sky-500 focus-visible:ring-offset-2 focus-visible:outline-none disabled:cursor-not-allowed disabled:bg-slate-400 dark:focus-visible:ring-offset-slate-900 dark:disabled:bg-slate-700"
      >
        {loading && (
          <svg aria-hidden="true" viewBox="0 0 24 24" className="size-4 animate-spin" fill="none">
            <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" opacity="0.25" />
            <path fill="currentColor" d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4z" />
          </svg>
        )}
        {loading ? 'Аналізуємо…' : 'Проаналізувати'}
      </button>
    </div>
  )
}

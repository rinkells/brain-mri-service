import { useEffect, useRef, useState, type FormEvent } from 'react'

import { predict } from './api/client'
import type { PredictionResponse } from './api/types'
import { ResultPanel } from './components/result/ResultPanel'
import { ErrorAlert } from './components/status/ErrorAlert'
import { HealthIndicator } from './components/status/HealthIndicator'
import { ThemeToggle } from './components/theme/ThemeToggle'
import { AnalyzeControls } from './components/upload/AnalyzeControls'
import { UploadZone } from './components/upload/UploadZone'
import { useHealth } from './hooks/useHealth'
import { useImageSelection } from './hooks/useImageSelection'
import { useTheme } from './hooks/useTheme'
import { validateImageFile } from './lib/validation'

type RequestState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'success'; result: PredictionResponse; scanUrl: string }
  | { status: 'error'; error: unknown }

export default function App() {
  const health = useHealth()
  const { theme, toggleTheme } = useTheme()
  const { selection, select, clear } = useImageSelection()
  const [validationError, setValidationError] = useState<string | null>(null)
  const [gradcam, setGradcam] = useState(false)
  const [request, setRequest] = useState<RequestState>({ status: 'idle' })
  const controllerRef = useRef<AbortController | null>(null)

  useEffect(() => () => controllerRef.current?.abort(), [])

  function resetRequest() {
    controllerRef.current?.abort()
    setRequest({ status: 'idle' })
  }

  function handleFile(file: File) {
    // A new file makes the previous result and its preview stale.
    resetRequest()
    const problem = validateImageFile(file)
    setValidationError(problem)
    if (problem) {
      clear()
    } else {
      select(file)
    }
  }

  function handleClear() {
    resetRequest()
    setValidationError(null)
    clear()
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selection) {
      return
    }

    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    setRequest({ status: 'loading' })

    try {
      const result = await predict(selection.file, { gradcam, signal: controller.signal })
      setRequest({ status: 'success', result, scanUrl: selection.previewUrl })
    } catch (error) {
      if (!controller.signal.aborted) {
        setRequest({ status: 'error', error })
      }
    }
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
      <header className="border-b border-slate-200 bg-white/80 dark:border-slate-800 dark:bg-slate-900/80">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-4">
          <div>
            <h1 className="text-xl font-semibold">Класифікація МРТ мозку</h1>
            <p className="text-sm text-slate-500 dark:text-slate-400">
              Гліома, менінгіома, пухлина гіпофіза або відсутність пухлини
            </p>
          </div>
          <div className="flex items-center gap-3">
            <HealthIndicator health={health} />
            <ThemeToggle theme={theme} onToggle={toggleTheme} />
          </div>
        </div>
      </header>

      <main className="mx-auto grid max-w-6xl gap-6 px-4 py-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <form
          onSubmit={handleSubmit}
          aria-label="Завантаження знімка"
          className="space-y-4 self-start rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900"
        >
          <h2 className="font-medium">Знімок</h2>
          <UploadZone
            selection={selection}
            error={validationError}
            onFile={handleFile}
            onClear={handleClear}
          />
          <AnalyzeControls
            gradcam={gradcam}
            onGradcamChange={setGradcam}
            canSubmit={selection !== null}
            loading={request.status === 'loading'}
          />
          {request.status === 'error' && <ErrorAlert error={request.error} />}
        </form>

        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900">
          {request.status === 'success' ? (
            <ResultPanel result={request.result} scanUrl={request.scanUrl} />
          ) : (
            <div className="flex h-full min-h-64 items-center justify-center text-center text-slate-500 dark:text-slate-400">
              {request.status === 'loading'
                ? 'Модель аналізує знімок…'
                : 'Оберіть знімок і натисніть «Проаналізувати». Тут з’явиться результат.'}
            </div>
          )}
        </div>
      </main>

      <footer className="mx-auto max-w-6xl px-4 pb-8 text-xs text-slate-500 dark:text-slate-400">
        Навчальний проєкт, а не медичний виріб. Результати не можна використовувати для діагностики
        чи лікування.
      </footer>
    </div>
  )
}

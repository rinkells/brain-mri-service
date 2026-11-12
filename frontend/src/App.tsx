import { AnalysisPage } from './components/analysis/AnalysisPage'
import { HistoryPage } from './components/history/HistoryPage'
import { ViewTabs } from './components/layout/ViewTabs'
import { HealthIndicator } from './components/status/HealthIndicator'
import { ThemeToggle } from './components/theme/ThemeToggle'
import { useHealth } from './hooks/useHealth'
import { useTheme } from './hooks/useTheme'
import { useView } from './hooks/useView'

export default function App() {
  const health = useHealth()
  const { theme, toggleTheme } = useTheme()
  const view = useView()

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
          <ViewTabs current={view} />
          <div className="flex items-center gap-3">
            <HealthIndicator health={health} />
            <ThemeToggle theme={theme} onToggle={toggleTheme} />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-6">
        {/* Kept mounted while hidden, so the chosen scan and result survive a visit to History. */}
        <div hidden={view !== 'analysis'}>
          <AnalysisPage />
        </div>
        {/* Mounted on each visit, so the history is always fresh. */}
        {view === 'history' && <HistoryPage />}
      </main>

      <footer className="mx-auto max-w-6xl px-4 pb-8 text-xs text-slate-500 dark:text-slate-400">
        Навчальний проєкт, а не медичний виріб. Результати не можна використовувати для діагностики
        чи лікування. В історії зберігаються лише результати прогнозів, а не самі знімки.
      </footer>
    </div>
  )
}
